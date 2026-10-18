"""Grounding detector: is each claim supported by the caller's sources?

One judge call per claim (P3: the judge sees that claim and the sources, never the
rest of the draft). The judge's quotes are re-checked by code (P2), and any model
failure becomes UNVERIFIABLE (P1).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from saife import prompts
from saife.config import Role
from saife.detectors.base import DetectorContext, enforce_evidence, unverifiable
from saife.errors import LLMError
from saife.evidence import check_quote
from saife.types import Claim, DetectorName, DetectorVerdict, Source


class JudgeQuote(BaseModel):
    source_id: str
    quote: str


class GroundingJudgement(BaseModel):
    """LLM output schema. Deliberately has no ``verified`` field (P2)."""

    verdict: Literal["SUPPORTED", "CONTRADICTED", "NOT_FOUND"]
    confidence: float = Field(ge=0.0, le=1.0)
    quotes: list[JudgeQuote] = Field(default_factory=list)
    rationale: str = ""


def build_prompt(claim: Claim, sources: list[Source]) -> str:
    blocks = [prompts.data_block("claim", claim.text)]
    blocks += [prompts.data_block("source", s.text, id=s.id) for s in sources]
    return "\n\n".join(blocks)


class GroundingDetector:
    name: DetectorName = "grounding"

    def applies(self, ctx: DetectorContext) -> bool:
        return bool(ctx.sources)

    async def check(self, claims: list[Claim], ctx: DetectorContext) -> list[DetectorVerdict]:
        return list(await asyncio.gather(*(self._judge(c, ctx) for c in claims)))

    async def _judge(self, claim: Claim, ctx: DetectorContext) -> DetectorVerdict:
        try:
            routed = await ctx.router.parse(
                Role.GROUNDING_JUDGE,
                system=prompts.load("grounding"),
                messages=[{"role": "user", "content": build_prompt(claim, ctx.sources)}],
                schema=GroundingJudgement,
                max_tokens=2048,
            )
        except LLMError as exc:
            return unverifiable(
                self.name,
                claim,
                f"{type(exc).__name__}: {exc}",
                model=f"{exc.provider}/{exc.model}",
            )
        judgement = ctx.record(routed).parsed
        urls = {s.id: s.url for s in ctx.sources}
        evidence = [
            check_quote(
                q.quote,
                source_id=q.source_id,
                source_text=ctx.source_text(q.source_id),
                url=urls.get(q.source_id),
            )
            for q in judgement.quotes
        ]
        verdict = DetectorVerdict(
            detector=self.name,
            claim_id=claim.id,
            verdict=judgement.verdict,
            confidence=judgement.confidence,
            support_kind="evidence",
            evidence=evidence,
            rationale=judgement.rationale or None,
            model=f"{routed.ref.provider}/{routed.ref.model}",
            fallback=routed.fallback is not None,
        )
        return enforce_evidence(verdict)
