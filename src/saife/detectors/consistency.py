"""Self-consistency detector (SelfCheckGPT idea): does the generator say the same thing
when asked again?

Agreement across resamples is weak support. Verdicts carry ``support_kind="consistency"``
and never any evidence, so aggregation caps them at LIKELY (PRD §4.3) and they can never
make a claim SUPPORTED on their own. Model failures become UNVERIFIABLE (P1).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from saife import prompts
from saife.config import Role
from saife.detectors.base import DetectorContext, enforce_evidence, unverifiable
from saife.errors import LLMError
from saife.types import Claim, DetectorName, DetectorVerdict

SAMPLE_SYSTEM = "Answer the user's question accurately and concisely."
SAMPLE_TEMPERATURE = 1.0
SAMPLE_MAX_TOKENS = 1024
MIN_SAMPLES = 2  # one sample cannot show (in)consistency
SUPPORT_RATIO = 2 / 3  # "≥ 0.67": 2 of 3 samples counts as agreement
_EPS = 1e-9

SampleVerdict = Literal["supports", "contradicts", "absent"]


class SampleJudgement(BaseModel):
    sample_id: str
    verdict: SampleVerdict


class ConsistencyJudgement(BaseModel):
    """LLM output schema: one verdict per sample. No evidence, no ``verified`` (P2)."""

    verdicts: list[SampleJudgement] = Field(default_factory=list)
    rationale: str = ""


def sample_id(i: int) -> str:
    return f"s{i + 1}"


def build_prompt(claim: Claim, samples: list[str]) -> str:
    blocks = [prompts.data_block("claim", claim.text)]
    blocks += [prompts.data_block("sample", s, id=sample_id(i)) for i, s in enumerate(samples)]
    return "\n\n".join(blocks)


def tally(judgement: ConsistencyJudgement, n: int) -> tuple[int, int]:
    """(supports, contradicts) over the ``n`` known samples. Unknown ids are ignored,
    duplicates count once (first wins) and missing samples count as absent."""
    known = {sample_id(i) for i in range(n)}
    seen: dict[str, SampleVerdict] = {}
    for v in judgement.verdicts:
        if v.sample_id in known and v.sample_id not in seen:
            seen[v.sample_id] = v.verdict
    votes = list(seen.values())
    return votes.count("supports"), votes.count("contradicts")


def map_ratio(
    supports: int, contradicts: int, n: int
) -> tuple[Literal["SUPPORTED", "CONTRADICTED", "NOT_FOUND"], float]:
    ratio = supports / n
    if ratio >= SUPPORT_RATIO - _EPS and contradicts == 0:
        return "SUPPORTED", ratio
    if contradicts * 2 > n:
        return "CONTRADICTED", contradicts / n
    return "NOT_FOUND", 1.0 - ratio


class ConsistencyDetector:
    name: DetectorName = "consistency"

    def __init__(self) -> None:
        # Samples answer the *question*, not the draft, so later passes reuse them.
        self._samples: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    def applies(self, ctx: DetectorContext) -> bool:
        return bool(ctx.question) and ctx.config.consistency_samples > 0

    async def check(self, claims: list[Claim], ctx: DetectorContext) -> list[DetectorVerdict]:
        if not claims:
            return []
        samples, error = await self._get_samples(ctx)
        if samples is None:
            return [unverifiable(self.name, c, error) for c in claims]
        return list(await asyncio.gather(*(self._judge(c, samples, ctx) for c in claims)))

    async def _get_samples(self, ctx: DetectorContext) -> tuple[list[str] | None, str]:
        question = ctx.question or ""
        async with self._lock:
            cached = self._samples.get(question)
            if cached is not None:
                return cached, ""
            n = ctx.config.consistency_samples
            results = await asyncio.gather(*(self._sample(question, ctx) for _ in range(n)))
            ok = [text for text, _ in results if text]
            if len(ok) < MIN_SAMPLES:
                errors = sorted({err for _, err in results if err})
                detail = "; ".join(errors) or "empty answers"
                # Not cached: a later pass may retry once the outage clears.
                return None, f"consistency sampling failed ({len(ok)}/{n} usable): {detail}"
            self._samples[question] = ok
            return ok, ""

    async def _sample(self, question: str, ctx: DetectorContext) -> tuple[str, str | None]:
        """(text, error). Empty text on failure or an empty answer."""
        try:
            routed = await ctx.router.complete(
                Role.GENERATE,
                system=SAMPLE_SYSTEM,
                messages=[{"role": "user", "content": question}],
                max_tokens=SAMPLE_MAX_TOKENS,
                temperature=SAMPLE_TEMPERATURE,
            )
        except LLMError as exc:
            return "", f"{type(exc).__name__}: {exc}"
        return ctx.record(routed).text.strip(), None

    async def _judge(
        self, claim: Claim, samples: list[str], ctx: DetectorContext
    ) -> DetectorVerdict:
        try:
            routed = await ctx.router.parse(
                Role.CONSISTENCY_JUDGE,
                system=prompts.load("consistency"),
                messages=[{"role": "user", "content": build_prompt(claim, samples)}],
                schema=ConsistencyJudgement,
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
        n = len(samples)
        supports, contradicts = tally(judgement, n)
        verdict, confidence = map_ratio(supports, contradicts, n)
        rationale = f"{supports}/{n} samples agree, {contradicts}/{n} contradict."
        if judgement.rationale:
            rationale = f"{rationale} {judgement.rationale}"
        return enforce_evidence(
            DetectorVerdict(
                detector=self.name,
                claim_id=claim.id,
                verdict=verdict,
                confidence=confidence,
                support_kind="consistency",
                evidence=[],  # never: agreement between samples is not evidence
                rationale=rationale,
                model=f"{routed.ref.provider}/{routed.ref.model}",
                fallback=routed.fallback is not None,
            )
        )
