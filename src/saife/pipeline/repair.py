"""Repair flagged claims with restricted, code-applied span edits (F5, P5).

The model only chooses an op and a replacement per claim. Spans come from the claim
(code), and every edit is validated here before it touches the draft:

- ``claim_id`` must be one of the flagged claims, and the claim must have a span.
- ``correct`` needs verified evidence, and the replacement must reuse it: it shares a
  >= 12-character normalised fragment with a verified quote, or it introduces new values
  (numbers); in both cases every number it introduces must appear in a verified quote.
- ``hedge`` must contain a hedge phrase (``HEDGE``) and must not introduce new numbers.
  Text that is already hedged is never hedged again.
- An invalid ``correct`` or ``hedge`` falls back to a code-built hedge: the original text
  plus an "(unverified)" / "(disputed by the sources)" marker. Code never invents content.
- ``remove`` deletes the whole sentence when every claim in it is flagged and nothing else
  is left (no orphaned fragments); otherwise it deletes the anchor, widened to swallow the
  space or punctuation it would orphan, or hedges it in code if the rest would be a fragment.
- Several hedges in one sentence hedge it once (duplicate adverbs and markers are dropped).

Overlapping edits keep the first by priority (remove > correct > hedge), then by position.
Accepted edits are applied in reverse span order, so earlier offsets stay valid, and each
becomes a ``RepairRecord`` whose ``before``/``after``/``span`` exactly invert the change.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Collection, Sequence

from pydantic import BaseModel, Field

from saife.config import Role
from saife.detectors.base import DetectorContext
from saife.evidence import MIN_QUOTE_CHARS
from saife.prompts import data_block, load
from saife.text import normalize
from saife.types import ClaimResult, ClaimStatus, Evidence, RepairOp, RepairRecord

log = logging.getLogger(__name__)


class EditOut(BaseModel):
    """Model output: one edit per flagged claim. No spans, no evidence flags."""

    claim_id: str
    op: RepairOp
    replacement: str = ""
    reason: str = ""


class RepairOut(BaseModel):
    edits: list[EditOut]


class SpanEdit(BaseModel):
    """A validated edit located in the draft by code."""

    claim_id: str
    op: RepairOp
    span: tuple[int, int]
    before: str
    after: str
    reason: str
    evidence: list[Evidence] = Field(default_factory=list)


class RepairOutcome(BaseModel):
    draft: str
    records: list[RepairRecord]
    rejected: list[str] = Field(default_factory=list)  # why edits were dropped or downgraded


PRIORITY: dict[RepairOp, int] = {"remove": 0, "correct": 1, "hedge": 2}
DEFAULT_REPAIR_STATUSES = ("CONTRADICTED", "UNSUPPORTED")

HEDGE = re.compile(
    r"\b(may|might|could|possibly|perhaps|reportedly|reported|allegedly|apparently|"
    r"unverified|unconfirmed|uncertain|unclear|approximately|roughly|"
    r"not (?:been )?(?:confirmed|verified)|(?:is|are|was|were) (?:thought|believed|claimed)|"
    r"according to some)\b"
)
MARKERS = (" (unverified)", " (disputed by the sources)")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_PUNCT = ".,;:!?"
_TRAIL = re.compile(r"[\s.,;:!?]*$")


def _norm(text: str) -> str:
    return normalize(html.unescape(text)).casefold()


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(normalize(text))}


def verified_evidence(result: ClaimResult) -> list[Evidence]:
    """Verified evidence from decisive verdicts only. Partial deterministic checks attach
    informational evidence to NOT_FOUND verdicts; that must not justify a ``correct``."""
    return [
        e
        for v in result.verdicts
        if v.verdict in ("SUPPORTED", "CONTRADICTED")
        for e in v.evidence
        if e.verified
    ]


def _shares_fragment(replacement: str, quote: str) -> bool:
    rep, q = _norm(replacement), _norm(quote)
    n = MIN_QUOTE_CHARS
    return any(q[i : i + n] in rep for i in range(len(q) - n + 1))


def _correct_ok(replacement: str, before: str, evidence: Sequence[Evidence]) -> bool:
    if not evidence or not replacement.strip():
        return False
    new = _numbers(replacement) - _numbers(before)
    quoted = set().union(*(_numbers(e.quote) for e in evidence))
    if not new <= quoted:
        return False
    return bool(new) or any(_shares_fragment(replacement, e.quote) for e in evidence)


def _hedge_ok(replacement: str, before: str) -> bool:
    if not replacement.strip() or not HEDGE.search(_norm(replacement)):
        return False
    return _numbers(replacement) <= _numbers(before)


def already_hedged(text: str) -> bool:
    return bool(HEDGE.search(_norm(text))) or any(m.strip() in text for m in MARKERS)


def code_hedge(before: str, status: ClaimStatus) -> str | None:
    """Original text plus a marker, placed before trailing punctuation/space."""
    tail = _TRAIL.search(before)
    cut = tail.start() if tail else len(before)
    core = before[:cut]
    if not core.strip():
        return None
    marker = MARKERS[1] if status == "CONTRADICTED" else MARKERS[0]
    return f"{core}{marker}{before[cut:]}"


def removal_span(draft: str, start: int, end: int) -> tuple[int, int]:
    """Widen a removal so it leaves no doubled space or orphaned punctuation."""
    n = len(draft)
    if (start == 0 or draft[start - 1] in " \t\n") and end < n and draft[end] in " \t":
        while end < n and draft[end] in " \t":
            end += 1
        return start, end
    if end == n or draft[end] in _PUNCT:
        while start > 0 and draft[start - 1] in " \t":
            start -= 1
        prev = draft[start - 1] if start > 0 else ""
        if prev in ("", "\n"):
            if end < n and draft[end] in _PUNCT:
                end += 1
                while end < n and draft[end] in " \t":
                    end += 1
        elif prev in ",;:":
            start -= 1  # the following punctuation takes its place
        elif prev in ".!?" and end < n and draft[end] in _PUNCT:
            end += 1
    return start, end


# --- Sentences -------------------------------------------------------------------------
# A deliberately simple splitter. A sentence ends at a newline, or at ``.``/``!``/``?``
# (plus closing quotes or brackets) followed by whitespace or the end of the text. A ``.``
# inside a token (``3.0``, ``json.loads``) is never an end; nor is one closing a known
# abbreviation (``Dr.``, ``etc.``), a dotted initialism (``e.g.``, ``U.S.``) or a single
# letter (``J. Smith``). Doubtful cases make sentences longer, which only makes
# whole-sentence removal rarer.
_END = re.compile(r"[.!?]+[\"')\]]*(?=\s|$)|\n")
_ABBREV = frozenset("mr mrs ms dr prof sr jr st vs etc inc ltd co corp no fig approx cf al".split())
_INITIALISM = re.compile(r"(?:\b[A-Za-z]\.){2,}$")


def _not_an_end(text: str, start: int, m: re.Match[str]) -> bool:
    if not m.group().startswith(".") or m.group().startswith(".."):
        return False
    words = text[start : m.start() + 1].split()
    if not words:
        return False
    token = words[-1]
    bare = token.rstrip(".").lower()
    return bare in _ABBREV or bool(_INITIALISM.search(token)) or (len(bare) == 1 and bare.isalpha())


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentence ``(start, end)`` spans, ``end`` just past the terminal punctuation; the
    whitespace between sentences belongs to none."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _END.finditer(text):
        if m.group() != "\n" and _not_an_end(text, start, m):
            continue
        _add_span(text, start, m.start() if m.group() == "\n" else m.end(), spans)
        start = m.end()
    _add_span(text, start, len(text), spans)
    return spans


def _add_span(text: str, start: int, end: int, spans: list[tuple[int, int]]) -> None:
    chunk = text[start:end]
    if chunk.strip():
        lead = len(chunk) - len(chunk.lstrip())
        spans.append((start + lead, start + len(chunk.rstrip())))


def sentence_at(text: str, pos: int) -> tuple[int, int] | None:
    for s, e in sentence_spans(text):
        if s <= pos < e:
            return s, e
    return None


def sentence_removal_span(draft: str, start: int, end: int) -> tuple[int, int]:
    """A whole sentence plus the spaces after it (at a line end: the spaces before it)."""
    n = len(draft)
    e = end
    while e < n and draft[e] in " \t":
        e += 1
    if e > end:
        return start, e
    s = start
    while s > 0 and draft[s - 1] in " \t":
        s -= 1
    if s == start and e < n and draft[e] == "\n" and (s == 0 or draft[s - 1] == "\n"):
        e += 1  # a sentence alone on its line takes its newline with it
    return s, e


# Words that may lie outside every flagged span without blocking whole-sentence removal:
# connectives make no claim of their own. Any other word might belong to a claim that passed.
_GLUE = frozenset(
    "a an the and but or so also then however moreover furthermore additionally which that".split()
)
# A leftover sentence starting with one of these (or lowercase) has lost its subject.
_VERBS = frozenset(
    "is are was were be been being has have had does do did can could will would shall should "
    "may might must remains remain became becomes runs works uses makes takes gives".split()
)


def _fragment(text: str) -> bool:
    words = re.findall(r"[A-Za-z][\w'-]*", text)
    return bool(words) and (words[0][0].islower() or words[0].lower() in _VERBS)


def _sentence_removable(
    draft: str,
    sent: tuple[int, int],
    flagged: Sequence[ClaimResult],
    ops: dict[str, RepairOp],
    repair_statuses: Collection[str],
) -> bool:
    """Every flagged claim in the sentence is removed or has a repair status (none was
    corrected from evidence), and together they cover every non-connective word."""
    ss, se = sent
    covered = [False] * (se - ss)
    for r in flagged:
        span = r.claim.span
        if span is None or not (span[0] < se and ss < span[1]):
            continue
        op = ops.get(r.claim.id)
        if op == "correct" or (op != "remove" and r.status not in repair_statuses):
            return False
        for i in range(max(span[0], ss), min(span[1], se)):
            covered[i - ss] = True
    rest = "".join(" " if c else ch for c, ch in zip(covered, draft[ss:se], strict=True))
    return all(w.lower() in _GLUE for w in re.findall(r"\w+", rest))


def _code_hedge_edit(
    claim_id: str, before: str, span: tuple[int, int], status: ClaimStatus
) -> SpanEdit | None:
    after = code_hedge(before, status)
    if after is None:
        return None
    return SpanEdit(
        claim_id=claim_id,
        op="hedge",
        span=span,
        before=before,
        after=after,
        reason="Could not be repaired from verified evidence; marked as uncertain.",
    )


def _place_removals(
    draft: str,
    edits: list[SpanEdit],
    flagged: Sequence[ClaimResult],
    repair_statuses: Collection[str],
    rejected: list[str],
) -> list[SpanEdit]:
    """Turn each removal's claim span into the span actually deleted.

    The whole enclosing sentence (with its trailing space) goes when ``_sentence_removable``;
    other edits inside that sentence are then dropped. Otherwise only the anchor goes, unless
    it opens the sentence and the rest would be a fragment: then it is hedged in code.
    """
    status = {r.claim.id: r.status for r in flagged}
    ops = {e.claim_id: e.op for e in edits}
    out: list[SpanEdit] = []
    removed: dict[tuple[int, int], str] = {}  # sentence -> claim whose edit removes it
    for ed in edits:
        if ed.op != "remove":
            out.append(ed)
            continue
        cs, ce = ed.span
        sent = sentence_at(draft, cs)
        if sent is not None and _sentence_removable(draft, sent, flagged, ops, repair_statuses):
            if sent in removed:
                rejected.append(f"{ed.claim_id}: removed with its sentence ({removed[sent]})")
                continue
            removed[sent] = ed.claim_id
            s, e = sentence_removal_span(draft, *sent)
            out.append(ed.model_copy(update={"span": (s, e), "before": draft[s:e]}))
            continue
        s, e = removal_span(draft, cs, ce)
        if sent is not None and s <= sent[0] and e < sent[1] and _fragment(draft[e : sent[1]]):
            fallback = None
            if not already_hedged(ed.before):
                fallback = _code_hedge_edit(ed.claim_id, ed.before, ed.span, status[ed.claim_id])
            if fallback is not None:
                rejected.append(
                    f"{ed.claim_id}: removal would leave a sentence fragment; "
                    "hedged in code instead"
                )
                out.append(fallback)
                continue
        out.append(ed.model_copy(update={"span": (s, e), "before": draft[s:e]}))
    keep: list[SpanEdit] = []
    for ed in out:
        hit = next(
            (
                cid
                for (ss, se), cid in removed.items()
                if cid != ed.claim_id and ed.span[0] < se and ss < ed.span[1]
            ),
            None,
        )
        if hit is not None:
            rejected.append(f"{ed.claim_id}: removed with its sentence ({hit})")
            continue
        keep.append(ed)
    return keep


# Hedge adverbs a later hedge can drop without breaking the grammar (modals cannot).
_ADVERBS = ("reportedly", "allegedly", "apparently", "possibly", "perhaps", "supposedly")


def _has(word: str, text: str) -> bool:
    return re.search(rf"\b{word}\b", text, re.IGNORECASE) is not None


def _drop_word(text: str, word: str, before: str) -> str:
    """Drop ``word`` and the space before it, restoring the anchor's leading space and case."""
    out = re.sub(rf"\s*\b{word}\b,?(?=\s|$|[.;:!?])", "", text, flags=re.IGNORECASE)
    lead = before[: len(before) - len(before.lstrip())]
    core, first = out.lstrip(), before.lstrip()[:1]
    if core and first and first.isupper() != core[0].isupper():
        core = (core[0].upper() if first.isupper() else core[0].lower()) + core[1:]
    return lead + core


def _dedupe_hedges(draft: str, edits: list[SpanEdit], rejected: list[str]) -> list[SpanEdit]:
    """Hedge each sentence once. In position order, a hedge drops any adverb an earlier hedge
    in the same sentence already added, and only the last code marker in a sentence is kept.
    A hedge left equal to its anchor is dropped (the sentence is hedged already)."""
    groups: dict[tuple[int, int], list[SpanEdit]] = {}
    for ed in edits:
        sent = sentence_at(draft, ed.span[0])
        if ed.op == "hedge" and sent is not None:
            groups.setdefault(sent, []).append(ed)
    changed: dict[int, SpanEdit | None] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: x.span)
        marked = [i for i, g in enumerate(group) if any(m in g.after for m in MARKERS)]
        used: set[str] = set()
        for i, ed in enumerate(group):
            after = ed.after
            for w in _ADVERBS:
                if _has(w, after) and not _has(w, ed.before):
                    if w in used:
                        after = _drop_word(after, w, ed.before)
                    used.add(w)
            if marked and i != marked[-1]:
                for m in MARKERS:
                    if m not in ed.before:
                        after = after.replace(m, "")
            if after == ed.before:
                rejected.append(
                    f"{ed.claim_id}: its sentence is already hedged by another edit; "
                    "duplicate hedge dropped"
                )
                changed[id(ed)] = None
            elif after != ed.after:
                changed[id(ed)] = ed.model_copy(update={"after": after})
    result: list[SpanEdit] = []
    for ed in edits:
        new = changed.get(id(ed), ed)
        if new is not None:
            result.append(new)
    return result


def resolve_edits(
    draft: str,
    edits: Sequence[EditOut],
    flagged: Sequence[ClaimResult],
    *,
    repair_statuses: Collection[str] = DEFAULT_REPAIR_STATUSES,
) -> tuple[list[SpanEdit], list[str]]:
    """Validate model edits against the flagged claims; return span edits and rejections."""
    by_id = {r.claim.id: r for r in flagged}
    out: list[SpanEdit] = []
    rejected: list[str] = []
    for edit in edits:
        # The model saw the draft HTML-escaped inside a data block (prompts.data_block) and
        # may copy escapes into its replacement; the draft itself is never escaped.
        edit = edit.model_copy(update={"replacement": html.unescape(edit.replacement)})
        result = by_id.get(edit.claim_id)
        if result is None:
            rejected.append(f"{edit.claim_id}: not a flagged claim; edit dropped")
            continue
        span = result.claim.span
        if span is None or not (0 <= span[0] < span[1] <= len(draft)):
            rejected.append(f"{edit.claim_id}: claim has no span in the draft; edit dropped")
            continue
        before = draft[span[0] : span[1]]
        reason = edit.reason.strip() or f"{edit.op} {result.status.lower()} claim"
        if edit.op == "remove":
            # The span actually deleted is decided by _place_removals.
            out.append(
                SpanEdit(
                    claim_id=edit.claim_id,
                    op="remove",
                    span=span,
                    before=before,
                    after="",
                    reason=reason,
                )
            )
            continue
        evidence = verified_evidence(result)
        if edit.op == "correct" and _correct_ok(edit.replacement, before, evidence):
            out.append(
                SpanEdit(
                    claim_id=edit.claim_id,
                    op="correct",
                    span=span,
                    before=before,
                    after=edit.replacement,
                    reason=reason,
                    evidence=evidence,
                )
            )
            continue
        if already_hedged(before):
            rejected.append(f"{edit.claim_id}: anchor is already hedged; {edit.op} skipped")
            continue
        if edit.op == "hedge" and _hedge_ok(edit.replacement, before):
            out.append(
                SpanEdit(
                    claim_id=edit.claim_id,
                    op="hedge",
                    span=span,
                    before=before,
                    after=edit.replacement,
                    reason=reason,
                )
            )
            continue
        why = (
            "correct without verified evidence"
            if edit.op == "correct" and not evidence
            else f"{edit.op} replacement failed validation"
        )
        fallback = _code_hedge_edit(edit.claim_id, before, span, result.status)
        if fallback is None:
            rejected.append(f"{edit.claim_id}: {why}; edit dropped")
            continue
        rejected.append(f"{edit.claim_id}: {why}; hedged in code instead")
        out.append(fallback)
    out = _place_removals(draft, out, flagged, repair_statuses, rejected)
    return _dedupe_hedges(draft, out, rejected), rejected


def apply_edits(
    draft: str, edits: Sequence[SpanEdit], *, pass_index: int
) -> tuple[str, list[RepairRecord]]:
    """Apply non-overlapping edits; records come back in ascending span order."""
    order = sorted(range(len(edits)), key=lambda i: (PRIORITY[edits[i].op], edits[i].span, i))
    accepted: list[SpanEdit] = []
    seen: set[str] = set()
    for i in order:
        edit = edits[i]
        s, e = edit.span
        if edit.claim_id in seen:
            log.info("repair: second edit for %s skipped", edit.claim_id)
            continue
        if not (0 <= s < e <= len(draft)) or draft[s:e] != edit.before:
            log.info("repair: edit for %s does not match the draft; skipped", edit.claim_id)
            continue
        if any(s < a.span[1] and a.span[0] < e for a in accepted):
            log.info("repair: edit for %s overlaps a higher-priority edit; skipped", edit.claim_id)
            continue
        accepted.append(edit)
        seen.add(edit.claim_id)
    text = draft
    for edit in sorted(accepted, key=lambda x: x.span[0], reverse=True):
        s, e = edit.span
        text = text[:s] + edit.after + text[e:]
    records = [
        RepairRecord(
            claim_id=edit.claim_id,
            op=edit.op,
            before=edit.before,
            after=edit.after,
            span=edit.span,
            reason=edit.reason,
            evidence=edit.evidence,
            pass_index=pass_index,
        )
        for edit in sorted(accepted, key=lambda x: x.span[0])
    ]
    return text, records


def _prompt(draft: str, flagged: Sequence[ClaimResult]) -> str:
    parts = ["Draft:", data_block("draft", draft), "", "Flagged claims:"]
    for r in flagged:
        assert r.claim.span is not None
        cid = r.claim.id
        parts.append(data_block("claim", r.claim.text, id=cid, status=r.status))
        parts.append(data_block("anchor", draft[r.claim.span[0] : r.claim.span[1]], claim_id=cid))
        for ev in verified_evidence(r):
            parts.append(data_block("evidence", ev.quote, claim_id=cid, source_id=ev.source_id))
    parts += ["", "Return one edit per flagged claim."]
    return "\n".join(parts)


async def repair(draft: str, flagged: list[ClaimResult], ctx: DetectorContext) -> RepairOutcome:
    """One repair call for all flagged claims. ``LLMError`` propagates (the loop stops with
    ``repair_failed`` and discloses the flagged claims unchanged)."""
    repairable = [r for r in flagged if r.claim.span is not None]
    if not repairable:
        return RepairOutcome(draft=draft, records=[])
    routed = await ctx.router.parse(
        Role.REPAIR,
        system=load("repair"),
        messages=[{"role": "user", "content": _prompt(draft, repairable)}],
        schema=RepairOut,
    )
    plan = ctx.record(routed).parsed
    edits, rejected = resolve_edits(
        draft, plan.edits, repairable, repair_statuses=ctx.config.repair_statuses
    )
    new_draft, records = apply_edits(draft, edits, pass_index=ctx.pass_index)
    applied = {r.claim_id for r in records}
    rejected += [
        f"{e.claim_id}: overlapped another edit or did not match the draft; skipped"
        for e in edits
        if e.claim_id not in applied
    ]
    for msg in rejected:
        log.info("repair: %s", msg)
    return RepairOutcome(draft=new_draft, records=records, rejected=rejected)
