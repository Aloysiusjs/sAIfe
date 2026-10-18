"""Detector protocol and shared helpers.

A detector takes claims and returns exactly one ``DetectorVerdict`` per claim it
was given, in any order. It must not raise on model failure: ``LLMError`` becomes
an ``UNVERIFIABLE`` verdict (fail closed, P1). The loop additionally wraps every
detector call, so even an unexpected exception cannot read as support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from saife.config import GuardConfig
from saife.llm.router import Routed, Router
from saife.types import (
    Claim,
    DetectorName,
    DetectorVerdict,
    FallbackRecord,
    RunUsage,
    Source,
)


@dataclass
class DetectorContext:
    draft: str
    config: GuardConfig
    router: Router
    question: str | None = None
    sources: list[Source] = field(default_factory=list)
    pass_index: int = 0
    usage: RunUsage = field(default_factory=RunUsage)
    fallbacks: list[FallbackRecord] = field(default_factory=list)

    def source_text(self, source_id: str) -> str | None:
        for s in self.sources:
            if s.id == source_id:
                return s.text
        return None

    def record[R: BaseModel](self, routed: Routed[R]) -> R:
        """Account a routed call's usage and any fallback; return the result."""
        result = routed.result
        usage = getattr(result, "usage", None)
        if usage is not None:
            self.usage.add(routed.ref.provider, routed.ref.model, usage)
        if routed.fallback is not None:
            fb = routed.fallback
            self.fallbacks.append(
                FallbackRecord(
                    role=fb.role.value,
                    from_model=f"{fb.from_ref.provider}/{fb.from_ref.model}",
                    to_model=f"{fb.to_ref.provider}/{fb.to_ref.model}",
                    reason=fb.reason,
                )
            )
        return result


class Detector(Protocol):
    name: DetectorName

    def applies(self, ctx: DetectorContext) -> bool:
        """Whether this detector can run at all in this context (e.g. grounding needs sources)."""
        ...

    async def check(self, claims: list[Claim], ctx: DetectorContext) -> list[DetectorVerdict]: ...


def unverifiable(
    detector: DetectorName, claim: Claim, error: str, *, model: str | None = None
) -> DetectorVerdict:
    return DetectorVerdict(
        detector=detector,
        claim_id=claim.id,
        verdict="UNVERIFIABLE",
        confidence=0.0,
        error=error,
        model=model,
    )


def enforce_evidence(v: DetectorVerdict) -> DetectorVerdict:
    """P2: SUPPORTED/CONTRADICTED backed by evidence needs at least one *verified* item.
    Otherwise downgrade to UNVERIFIABLE with a reason. Consistency verdicts are left
    alone (aggregation caps them at LIKELY)."""
    if v.support_kind == "consistency" or v.verdict not in ("SUPPORTED", "CONTRADICTED"):
        return v
    if v.has_verified_evidence:
        return v
    return v.model_copy(
        update={
            "verdict": "UNVERIFIABLE",
            "confidence": 0.0,
            "error": f"{v.verdict} downgraded: evidence could not be confirmed in the source",
        }
    )
