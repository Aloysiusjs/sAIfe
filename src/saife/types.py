"""Core data model shared by the pipeline, detectors, server, trace and UI.

Safety rule 2: ``Evidence.verified`` is set only by ``saife.evidence`` (code),
never parsed from model output. LLM output schemas live next to the code that
calls the model and never contain ``Evidence``.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field

from saife.llm.base import Usage
from saife.text import normalize

ClaimKind = Literal["entity", "numeric", "date", "citation", "causal", "code_api", "opinion"]
CLAIM_KINDS: tuple[ClaimKind, ...] = (
    "entity",
    "numeric",
    "date",
    "citation",
    "causal",
    "code_api",
    "opinion",
)

Verdict = Literal["SUPPORTED", "CONTRADICTED", "NOT_FOUND", "UNVERIFIABLE"]
ClaimStatus = Literal["SUPPORTED", "CONTRADICTED", "LIKELY", "UNSUPPORTED", "UNVERIFIABLE"]
AnswerStatus = Literal["VERIFIED", "CAUTION", "BLOCKED"]
DetectorName = Literal["grounding", "consistency", "web_evidence", "deterministic"]

# How a detector supports a claim. Only "evidence" and "computed" can make a claim
# SUPPORTED/CONTRADICTED; "consistency" support caps at LIKELY (PRD §4.3).
SupportKind = Literal["evidence", "computed", "consistency"]

RepairOp = Literal["correct", "hedge", "remove"]
Severity = Literal["info", "warning", "high"]
StopCause = Literal[
    "resolved",  # no high-risk claims left
    "max_passes",
    "token_budget",
    "timeout",
    "not_converging",  # flagged set did not strictly shrink
    "repair_disabled",  # preset has no repair (fast)
    "repair_failed",
    "decompose_failed",  # claim extraction failed; nothing (more) could be checked
]


def claim_id(text: str) -> str:
    """Stable ID: same claim text (ignoring case and whitespace/quote variants) → same ID."""
    digest = hashlib.sha256(normalize(text).casefold().encode("utf-8")).hexdigest()
    return f"c_{digest[:12]}"


class Source(BaseModel):
    """A caller-supplied document (RAG mode) or a retrieved web result."""

    id: str
    text: str
    title: str | None = None
    url: str | None = None


class Claim(BaseModel):
    id: str
    text: str  # self-contained restatement of the claim
    anchor: str  # exact substring of the draft the claim came from
    span: tuple[int, int] | None  # char offsets of anchor in the draft; None = could not locate
    kind: ClaimKind
    checkworthy: bool = True

    @classmethod
    def make(
        cls,
        text: str,
        *,
        anchor: str,
        span: tuple[int, int] | None,
        kind: ClaimKind,
        checkworthy: bool = True,
    ) -> Claim:
        return cls(
            id=claim_id(text),
            text=text,
            anchor=anchor,
            span=span,
            kind=kind,
            checkworthy=checkworthy,
        )


class Evidence(BaseModel):
    source_id: str  # source id, URL, or "deterministic:<check>"
    quote: str  # exact quote, or a description of the computation for computed evidence
    url: str | None = None
    method: Literal["quote", "computed"] = "quote"
    # Set only by saife.evidence.check_quote / computed(). Never by an LLM.
    verified: bool = False


class DetectorVerdict(BaseModel):
    detector: DetectorName
    claim_id: str
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    support_kind: SupportKind = "evidence"
    evidence: list[Evidence] = Field(default_factory=list)
    rationale: str | None = None
    error: str | None = None  # set when the verdict is UNVERIFIABLE because of a failure
    model: str | None = None  # "provider/model" that judged, if any
    fallback: bool = False  # the verifier call fell back to the generator's provider

    @property
    def has_verified_evidence(self) -> bool:
        return any(e.verified for e in self.evidence)


class ClaimResult(BaseModel):
    claim: Claim
    status: ClaimStatus
    verdicts: list[DetectorVerdict] = Field(default_factory=list)
    risk: float = Field(ge=0.0, le=1.0)  # 0 = safe, 1 = certainly wrong
    pass_index: int = 0  # pass in which this claim was (last) verified


class RepairRecord(BaseModel):
    claim_id: str
    op: RepairOp
    before: str  # exact text replaced in the draft
    after: str  # replacement ("" for remove)
    span: tuple[int, int]  # span of `before` in the pre-repair draft
    reason: str
    evidence: list[Evidence] = Field(default_factory=list)  # verified evidence used by `correct`
    pass_index: int


class Alert(BaseModel):
    severity: Severity
    claim_ids: list[str] = Field(default_factory=list)
    # Copy rule (PRD §4.5): what was affected, what sAIfe did, what the user should do.
    message: str


class FallbackRecord(BaseModel):
    role: str
    from_model: str  # "provider/model"
    to_model: str
    reason: str


class PassRecord(BaseModel):
    index: int  # 0-based
    draft: str  # draft verified in this pass
    claim_ids: list[str]  # all claims in this pass's draft
    verified_ids: list[str]  # claims actually (re)verified this pass (delta)
    flagged_ids: list[str]  # claims needing repair after aggregation
    repairs: list[RepairRecord] = Field(default_factory=list)
    duration_s: float = 0.0


class RunUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    wall_time_s: float = 0.0
    by_model: dict[str, Usage] = Field(default_factory=dict)  # "provider/model" -> tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, provider: str, model: str, usage: Usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.calls += 1
        key = f"{provider}/{model}"
        prev = self.by_model.get(key, Usage())
        self.by_model[key] = Usage(
            input_tokens=prev.input_tokens + usage.input_tokens,
            output_tokens=prev.output_tokens + usage.output_tokens,
        )


class Report(BaseModel):
    run_id: str
    status: AnswerStatus
    preset: str
    question: str | None = None
    original_draft: str
    final_answer: str  # what the user is shown (abstention text when BLOCKED)
    claims: list[ClaimResult]  # claims of the final answer, plus removed ones
    passes: list[PassRecord]
    repairs: list[RepairRecord]
    alerts: list[Alert]
    usage: RunUsage
    risk: float = Field(ge=0.0, le=1.0)
    stop_cause: StopCause
    verifier_diversity: bool = True
    fallbacks: list[FallbackRecord] = Field(default_factory=list)
    trace_path: str | None = None


EventType = Literal[
    "run.started",
    "pass.started",
    "claim.extracted",
    "claim.verdict",
    "repair.applied",
    "alert.raised",
    "run.completed",
    "run.failed",
]


class Event(BaseModel):
    """One step of a run. ``data`` payloads per type:

    run.started      {question, preset, mode: "generate"|"answer", sources: [id]}
    pass.started     {index, draft}
    claim.extracted  {pass_index, claim: Claim}
    claim.verdict    {pass_index, result: ClaimResult}
    repair.applied   {pass_index, repair: RepairRecord, draft: str}
    alert.raised     {alert: Alert}
    run.completed    {status, report: Report}
    run.failed       {error: str}
    """

    type: EventType
    run_id: str
    seq: int  # 0-based, strictly increasing per run
    ts: float  # unix seconds
    data: dict[str, Any] = Field(default_factory=dict)
