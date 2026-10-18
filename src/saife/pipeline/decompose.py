"""Split a draft into atomic claims (F1).

The model returns claim text plus an ``anchor`` copied from the draft; character
spans are computed here by locating the anchor, never taken from the model, so a
span always slices back to real draft text. An anchor that can't be found gets
``span=None`` (and keeps the model's anchor) and the loop reports it as UNVERIFIABLE.
"""

from __future__ import annotations

import html
import logging

from pydantic import BaseModel

from saife.config import Role
from saife.detectors.base import DetectorContext
from saife.prompts import data_block, load
from saife.text import normalize
from saife.types import Claim, ClaimKind

log = logging.getLogger(__name__)


class ClaimOut(BaseModel):
    text: str
    anchor: str
    kind: ClaimKind
    checkworthy: bool


class DecomposeOut(BaseModel):
    """Model output schema. Flat and union-free so structured output stays reliable."""

    claims: list[ClaimOut]


class _NormIndex:
    """The draft normalised like ``saife.text.normalize``, with a map back to original offsets.

    Normalisation runs per character (NFKC, straight quotes/hyphens, zero-width removed,
    whitespace runs collapsed to one space), so each output character knows which
    original character produced it.
    """

    def __init__(self, draft: str) -> None:
        chars: list[str] = []
        starts: list[int] = []
        ends: list[int] = []
        for i, c in enumerate(draft):
            if c.isspace():
                if chars and chars[-1] == " ":
                    ends[-1] = i + 1
                    continue
                pieces = " "
            else:
                pieces = normalize(c)
            for p in pieces:
                chars.append(p)
                starts.append(i)
                ends.append(i + 1)
        self.text = "".join(chars)
        self._starts = starts
        self._ends = ends

    def find(self, needle: str, start: int) -> tuple[int, int] | None:
        """First match of normalised ``needle`` starting at original offset >= ``start``."""
        if not needle:
            return None
        pos = 0
        while (j := self.text.find(needle, pos)) != -1:
            if self._starts[j] >= start:
                return self._starts[j], self._ends[j + len(needle) - 1]
            pos = j + 1
        return None


class _Locator:
    """Finds anchors in the draft. A repeated anchor takes the next occurrence; when there
    is none left it reuses the last one (two claims from one sentence share an anchor)."""

    def __init__(self, draft: str) -> None:
        self.draft = draft
        self._norm: _NormIndex | None = None
        self._last: dict[str, tuple[int, int]] = {}

    def _find(self, anchor: str, start: int) -> tuple[int, int] | None:
        candidates = dict.fromkeys([anchor, html.unescape(anchor)])
        for cand in candidates:
            if cand and (i := self.draft.find(cand, start)) != -1:
                return i, i + len(cand)
        if self._norm is None:
            self._norm = _NormIndex(self.draft)
        for cand in candidates:
            if span := self._norm.find(normalize(cand), start):
                return span
        return None

    def locate(self, anchor: str) -> tuple[int, int] | None:
        key = normalize(html.unescape(anchor))
        if not key:
            return None
        prev = self._last.get(key)
        span = self._find(anchor, prev[0] + 1 if prev else 0) or prev
        if span is not None:
            self._last[key] = span
        return span


async def decompose(draft: str, ctx: DetectorContext) -> list[Claim]:
    """Extract claims from ``draft``. Raises ``LLMError`` on model failure."""
    if not draft.strip():
        return []
    routed = await ctx.router.parse(
        Role.DECOMPOSE,
        system=load("decompose"),
        messages=[
            {
                "role": "user",
                "content": "Split this draft into claims.\n\n" + data_block("draft", draft),
            }
        ],
        schema=DecomposeOut,
    )
    out = ctx.record(routed).parsed

    locator = _Locator(draft)
    claims: list[Claim] = []
    seen: set[str] = set()
    for c in out.claims:
        text = c.text.strip()
        if not text:
            continue
        opinion = c.kind == "opinion"
        claim = Claim.make(
            text,
            anchor=c.anchor,
            span=None,
            kind=c.kind,
            checkworthy=c.checkworthy and not opinion,
        )
        if claim.id in seen:
            continue
        seen.add(claim.id)
        span = locator.locate(c.anchor)
        if span is None:
            log.warning("decompose: anchor not found in draft for claim %s", claim.id)
            claims.append(claim)
        else:
            # Store the draft's own text, so anchor == draft[span] even after a fuzzy match.
            anchor = draft[span[0] : span[1]]
            claims.append(claim.model_copy(update={"span": span, "anchor": anchor}))
    return claims
