"""External evidence (PRD §4.3): Claude web search retrieves, the verifier judges.

Only text the search API returned for a page (citations' ``cited_text``) can back a
verdict. The model's own summary is never evidence, and every quote the judge picks
is checked mechanically against the hit it names before the verdict can stand.

Claims are searched in batches: one retrieval call per ``BATCH_SIZE`` claims, then one
judge call per claim over the batch's results. A search call costs mostly the fixed
overhead of reading result pages, so sharing it cuts per-claim cost.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from saife.config import Role
from saife.detectors.base import DetectorContext, enforce_evidence, unverifiable
from saife.errors import LLMError
from saife.evidence import check_quote
from saife.llm.base import SearchHit
from saife.prompts import data_block, load
from saife.types import Claim, DetectorName, DetectorVerdict, Evidence

NAME: DetectorName = "web_evidence"
MAX_HITS = 16  # keeps the judge prompt bounded (passages are short)
BATCH_SIZE = 4  # claims per search call


class WebQuote(BaseModel):
    hit_id: str
    quote: str


class WebJudgement(BaseModel):
    """Judge output schema. Deliberately has no ``verified`` field (P2)."""

    verdict: Literal["SUPPORTED", "CONTRADICTED", "NOT_FOUND"]
    confidence: float
    quotes: list[WebQuote] = Field(default_factory=list)
    rationale: str = ""


def _search_message(claims: list[Claim]) -> str:
    blocks = [data_block("claim", c.text, id=f"c{i}") for i, c in enumerate(claims, start=1)]
    return "\n\n".join(["Find web evidence about these claims.", *blocks])


def _judge_message(claim: Claim, hits: dict[str, SearchHit]) -> str:
    blocks = [
        data_block("search_result", h.text, id=hid, url=h.url, title=h.title)
        for hid, h in hits.items()
    ]
    return "\n\n".join([data_block("claim", claim.text), *blocks])


class WebEvidenceDetector:
    name: DetectorName = NAME

    def applies(self, ctx: DetectorContext) -> bool:
        return "web_evidence" in ctx.config.detectors

    async def check(self, claims: list[Claim], ctx: DetectorContext) -> list[DetectorVerdict]:
        out: dict[str, DetectorVerdict] = {}
        todo: list[Claim] = []
        if not ctx.router.can_search(Role.WEB_RETRIEVAL):  # e.g. verifier-only routing
            reason = "web search unavailable: no search-capable model is configured"
            return [unverifiable(NAME, c, reason) for c in claims]
        for c in claims:
            if not c.checkworthy or c.kind == "opinion":
                out[c.id] = unverifiable(NAME, c, "not check-worthy; web search skipped")
            elif c.id not in {t.id for t in todo}:
                todo.append(c)
        batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
        for verdicts in await asyncio.gather(*(self._check_batch(b, ctx) for b in batches)):
            out.update((v.claim_id, v) for v in verdicts)
        return [out[c.id] for c in claims]

    async def _check_batch(
        self, claims: list[Claim], ctx: DetectorContext
    ) -> list[DetectorVerdict]:
        try:
            routed = await ctx.router.search(
                Role.WEB_RETRIEVAL,
                system=load("web_search"),
                messages=[{"role": "user", "content": _search_message(claims)}],
                max_uses=ctx.config.web_max_uses + len(claims) - 1,
            )
        except LLMError as exc:
            model = f"{exc.provider}/{exc.model}"
            return [unverifiable(NAME, c, f"web search failed: {exc}", model=model) for c in claims]
        result = ctx.record(routed)
        readable = [h for h in result.hits if h.text.strip()][:MAX_HITS]
        if not readable:
            reason = "web search returned no readable results"
            return [unverifiable(NAME, c, reason) for c in claims]
        hits = {f"w{i}": h for i, h in enumerate(readable, start=1)}
        return list(await asyncio.gather(*(self._judge(c, hits, ctx) for c in claims)))

    async def _judge(
        self, claim: Claim, hits: dict[str, SearchHit], ctx: DetectorContext
    ) -> DetectorVerdict:
        try:
            judged = await ctx.router.parse(
                Role.WEB_JUDGE,
                system=load("web_judge"),
                messages=[{"role": "user", "content": _judge_message(claim, hits)}],
                schema=WebJudgement,
            )
        except LLMError as exc:
            return unverifiable(
                NAME, claim, f"web judge failed: {exc}", model=f"{exc.provider}/{exc.model}"
            )
        judgement = ctx.record(judged).parsed

        evidence: list[Evidence] = []
        for q in judgement.quotes:
            hit = hits.get(q.hit_id)
            if hit is None:  # unknown id: kept for the record, never verified
                evidence.append(check_quote(q.quote, source_id=q.hit_id, source_text=None))
            else:
                evidence.append(
                    check_quote(q.quote, source_id=hit.url, source_text=hit.text, url=hit.url)
                )
        verdict = DetectorVerdict(
            detector=NAME,
            claim_id=claim.id,
            verdict=judgement.verdict,
            confidence=min(max(judgement.confidence, 0.0), 1.0),
            evidence=evidence,
            rationale=judgement.rationale or None,
            model=f"{judged.ref.provider}/{judged.ref.model}",
            fallback=judged.fallback is not None,
        )
        return enforce_evidence(verdict)
