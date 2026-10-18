"""Orchestrator tests with fake collaborators: every stop cause, delta re-verification,
fail-closed handling of detector/decompose/generate failures, and event invariants."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from saife.config import ANTHROPIC, PRESETS, VERIFIER, FallbackPolicy, GuardConfig, default_roles
from saife.detectors.base import DetectorContext
from saife.errors import LLMOutputError, LLMUnavailableError
from saife.events import EventBus
from saife.evidence import computed
from saife.llm.base import Usage
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse
from saife.llm.router import Router
from saife.pipeline.loop import EDITS_UNCHECKED, NOTHING_VERIFIED, Pipeline, estimate_cost
from saife.pipeline.repair import RepairOutcome
from saife.trace import TraceSink, verify_trace
from saife.types import (
    Claim,
    ClaimKind,
    ClaimResult,
    DetectorName,
    DetectorVerdict,
    Evidence,
    FallbackRecord,
    RepairOp,
    RepairRecord,
    Report,
    RunUsage,
)

# --- fakes ------------------------------------------------------------------------------


def _no_llm(req: FakeRequest) -> FakeResponse:
    raise AssertionError(f"unexpected LLM call: {req.kind}")


def make_router() -> Router:
    return Router(
        {
            ANTHROPIC: FakeLLM(ANTHROPIC, handler=_no_llm),
            VERIFIER: FakeLLM(VERIFIER, handler=_no_llm),
        },
        default_roles(),
    )


SENTENCE = re.compile(r"[^.]+\.")


class FakeDecompose:
    """One claim per sentence. ``fail_on`` = call numbers (1-based) that raise."""

    def __init__(self, *, fail_on: tuple[int, ...] = (), tokens: int = 0) -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on
        self.tokens = tokens

    async def __call__(self, draft: str, ctx: DetectorContext) -> list[Claim]:
        self.calls.append(draft)
        if self.tokens:
            ctx.usage.add(ANTHROPIC, "claude-opus-5", Usage(input_tokens=self.tokens))
        if len(self.calls) in self.fail_on:
            raise LLMOutputError("bad decomposition", provider=ANTHROPIC, model="claude-opus-5")
        claims = []
        for m in SENTENCE.finditer(draft):
            text = m.group().strip()
            start = draft.index(text, m.start())
            claims.append(
                Claim.make(text, anchor=text, span=(start, start + len(text)), kind="entity")
            )
        return claims


class FakeDetector:
    """Verdict by claim text prefix: 'Good' → SUPPORTED (verified), 'Bad' → CONTRADICTED
    (verified), anything else → NOT_FOUND."""

    def __init__(self, name: DetectorName = "deterministic", **behaviour: Any) -> None:
        self.name = name
        self.checked: list[list[str]] = []
        self.behaviour = behaviour
        self.contexts: list[DetectorContext] = []

    def applies(self, ctx: DetectorContext) -> bool:
        return True

    async def check(self, claims: list[Claim], ctx: DetectorContext) -> list[DetectorVerdict]:
        self.checked.append([c.text for c in claims])
        self.contexts.append(ctx)
        if self.behaviour.get("hang"):
            await asyncio.sleep(3600)
        if self.behaviour.get("crash"):
            raise RuntimeError("detector exploded")
        if self.behaviour.get("fallback"):
            ctx.fallbacks.append(
                FallbackRecord(role="grounding_judge", from_model="v/m", to_model="a/m", reason="x")
            )
        if self.behaviour.get("forged"):
            return [
                DetectorVerdict(
                    detector=self.name,
                    claim_id=c.id,
                    verdict="SUPPORTED",
                    confidence=1.0,
                    evidence=[Evidence(source_id="s", quote="made up", verified=False)],
                )
                for c in claims
            ]
        if self.behaviour.get("partial"):
            claims = claims[:-1]
        return [self._verdict(c) for c in claims]

    def _verdict(self, c: Claim) -> DetectorVerdict:
        if c.text.startswith("Good"):
            return DetectorVerdict(
                detector=self.name,
                claim_id=c.id,
                verdict="SUPPORTED",
                confidence=1.0,
                support_kind="computed",
                evidence=[computed("test", "recomputed")],
            )
        if c.text.startswith("Bad"):
            return DetectorVerdict(
                detector=self.name,
                claim_id=c.id,
                verdict="CONTRADICTED",
                confidence=1.0,
                support_kind="computed",
                evidence=[computed("test", "recomputed")],
            )
        return DetectorVerdict(
            detector=self.name, claim_id=c.id, verdict="NOT_FOUND", confidence=0.5
        )


class FakeRepair:
    """Replaces each flagged claim's text using ``rewrites`` (old text → new text), or only
    a word of it when the value is a ``(word, new_word)`` pair."""

    def __init__(
        self,
        rewrites: dict[str, str | tuple[str, str]],
        *,
        error: Exception | None = None,
        op: RepairOp = "hedge",
    ) -> None:
        self.rewrites = rewrites
        self.error = error
        self.op = op
        self.calls = 0

    async def __call__(
        self, draft: str, flagged: list[ClaimResult], ctx: DetectorContext
    ) -> RepairOutcome:
        self.calls += 1
        if self.error is not None:
            raise self.error
        records = []
        for r in flagged:
            rewrite = self.rewrites.get(r.claim.text)
            if rewrite is None or r.claim.span is None:
                continue
            start = draft.index(r.claim.text)
            before, new = (r.claim.text, rewrite) if isinstance(rewrite, str) else rewrite
            start += r.claim.text.index(before)
            records.append(
                RepairRecord(
                    claim_id=r.claim.id,
                    op="remove" if new == "" else self.op,
                    before=before,
                    after=new,
                    span=(start, start + len(before)),
                    reason="test",
                    pass_index=ctx.pass_index,
                )
            )
            draft = draft[:start] + new + draft[start + len(before) :]
        return RepairOutcome(draft=" ".join(draft.split()), records=records)


class ScriptedDecompose:
    """Pass N returns ``script[N]``: (anchor, claim text, kind) triples, located in the draft."""

    def __init__(self, *script: list[tuple[str, str, ClaimKind]]) -> None:
        self.script = script
        self.calls: list[str] = []

    async def __call__(self, draft: str, ctx: DetectorContext) -> list[Claim]:
        self.calls.append(draft)
        out = []
        for anchor, text, kind in self.script[len(self.calls) - 1]:
            start = draft.index(anchor)
            out.append(
                Claim.make(text, anchor=anchor, span=(start, start + len(anchor)), kind=kind)
            )
        return out


class ScriptedRepair:
    """Edits a flagged claim's anchor text: ``edits[anchor] = (op, replacement)``."""

    def __init__(self, edits: dict[str, tuple[RepairOp, str]]) -> None:
        self.edits = edits
        self.calls = 0

    async def __call__(
        self, draft: str, flagged: list[ClaimResult], ctx: DetectorContext
    ) -> RepairOutcome:
        self.calls += 1
        records = []
        for r in flagged:
            assert r.claim.span is not None
            s, e = r.claim.span
            if draft[s:e] in self.edits:
                op, after = self.edits[draft[s:e]]
                records.append(
                    RepairRecord(
                        claim_id=r.claim.id,
                        op=op,
                        before=draft[s:e],
                        after=after,
                        span=(s, e),
                        reason="test",
                        pass_index=ctx.pass_index,
                    )
                )
        text = draft
        for rec in sorted(records, key=lambda x: x.span[0], reverse=True):
            text = text[: rec.span[0]] + rec.after + text[rec.span[1] :]
        return RepairOutcome(draft=text, records=sorted(records, key=lambda x: x.span[0]))


async def fake_generate(question: str, ctx: DetectorContext) -> str:
    return "Good one. Good two."


def config(preset: str = "balanced", **overrides: Any) -> GuardConfig:
    return PRESETS[preset].with_overrides(**overrides) if overrides else PRESETS[preset]


async def run(
    tmp_path: Path,
    *,
    answer: str | None = "Good one. Unknown two.",
    question: str | None = None,
    cfg: GuardConfig | None = None,
    detectors: list[FakeDetector] | None = None,
    decompose: FakeDecompose | ScriptedDecompose | None = None,
    repair: FakeRepair | ScriptedRepair | None = None,
    generate: Any = fake_generate,
) -> tuple[Report, EventBus, Path]:
    bus = EventBus("run123")
    trace = tmp_path / "run123.jsonl"
    bus.add_sink(TraceSink(trace))
    pipeline = Pipeline(
        make_router(),
        cfg or config(),
        detectors=detectors if detectors is not None else [FakeDetector()],
        decompose_fn=decompose or FakeDecompose(),
        repair_fn=repair or FakeRepair({}),
        generate_fn=generate,
        bus=bus,
    )
    report = await pipeline.run(answer=answer, question=question)
    return report, bus, trace


def assert_consistent(bus: EventBus, trace: Path) -> None:
    types = [e.type for e in bus.history]
    assert types[0] == "run.started"
    assert sum(t in ("run.completed", "run.failed") for t in types) == 1
    assert types[-1] in ("run.completed", "run.failed")
    if types[-1] == "run.completed":
        assert verify_trace(trace) == []


def by_text(report: Report) -> dict[str, ClaimResult]:
    return {r.claim.text: r for r in report.claims}


# --- stop causes ------------------------------------------------------------------------


async def test_all_supported_resolves_in_one_pass(tmp_path: Path) -> None:
    report, bus, trace = await run(tmp_path, answer="Good one. Good two.")
    assert report.status == "VERIFIED"
    assert report.stop_cause == "resolved"
    assert len(report.passes) == 1
    assert report.final_answer == report.original_draft == "Good one. Good two."
    assert report.verifier_diversity
    assert_consistent(bus, trace)
    kinds = {e.type for e in bus.history}
    assert {"run.started", "pass.started", "claim.extracted", "claim.verdict"} <= kinds


async def test_delta_reverifies_only_new_claims_and_updates_moved_spans(tmp_path: Path) -> None:
    det = FakeDetector()
    decomp = FakeDecompose()
    repair = FakeRepair({"Unknown one.": "Good maybe one."}, op="correct")
    report, bus, trace = await run(
        tmp_path,
        answer="Unknown one. Good two.",
        detectors=[det],
        decompose=decomp,
        repair=repair,
    )
    assert det.checked == [["Unknown one.", "Good two."], ["Good maybe one."]]
    assert report.stop_cause == "resolved"
    assert report.status == "VERIFIED"
    assert report.final_answer == "Good maybe one. Good two."
    claims = by_text(report)
    good_two = claims["Good two."]
    assert good_two.pass_index == 0  # kept its earlier result
    assert good_two.claim.span is not None
    s, e = good_two.claim.span
    assert report.final_answer[s:e] == "Good two."
    assert claims["Unknown one."].status == "UNSUPPORTED"  # removed claim stays in the report
    assert len(report.repairs) == 1 and report.passes[0].repairs == report.repairs
    assert report.passes[1].verified_ids == [claims["Good maybe one."].claim.id]
    assert_consistent(bus, trace)


async def test_max_passes(tmp_path: Path) -> None:
    # A word-level hedge: the next claim only partly overlaps it, so it is flagged again.
    repair = FakeRepair({"Unknown a.": ("a", "b")})
    report, bus, trace = await run(
        tmp_path,
        answer="Good one. Unknown a.",
        repair=repair,
        cfg=config(budgets={"max_passes": 2}),
    )
    assert report.stop_cause == "max_passes"
    assert len(report.passes) == 2
    assert report.status == "CAUTION"
    assert_consistent(bus, trace)


async def test_oscillating_repair_stops_on_convergence(tmp_path: Path) -> None:
    repair = FakeRepair({"Unknown a.": ("a", "b"), "Unknown b.": ("b", "a")})
    decomp = FakeDecompose()
    report, bus, trace = await run(
        tmp_path,
        answer="Unknown a.",
        repair=repair,
        decompose=decomp,
        cfg=config(budgets={"max_passes": 6}),
    )
    assert report.stop_cause == "not_converging"
    assert len(decomp.calls) == 2
    assert repair.calls == 1
    assert report.status == "CAUTION"
    assert_consistent(bus, trace)


async def test_repair_that_changes_nothing_stops(tmp_path: Path) -> None:
    report, _, _ = await run(tmp_path, answer="Unknown a.", repair=FakeRepair({}))
    assert report.stop_cause == "not_converging"
    assert len(report.passes) == 1


async def test_repair_disabled_in_fast(tmp_path: Path) -> None:
    repair = FakeRepair({"Unknown two.": ""})
    report, bus, trace = await run(tmp_path, cfg=config("fast"), repair=repair)
    assert report.stop_cause == "repair_disabled"
    assert repair.calls == 0
    assert report.status == "CAUTION"
    assert_consistent(bus, trace)


async def test_repair_failure_keeps_draft_and_alerts(tmp_path: Path) -> None:
    err = LLMUnavailableError("down", provider=ANTHROPIC, model="claude-opus-5")
    report, bus, trace = await run(tmp_path, repair=FakeRepair({}, error=err))
    assert report.stop_cause == "repair_failed"
    assert report.final_answer == report.original_draft
    assert report.status == "CAUTION"
    assert any("repair step failed" in a.message for a in report.alerts)
    assert_consistent(bus, trace)


async def test_token_budget_marks_unverified_claims(tmp_path: Path) -> None:
    det = FakeDetector()
    report, bus, trace = await run(
        tmp_path,
        detectors=[det],
        decompose=FakeDecompose(tokens=1_000),
        cfg=config(budgets={"max_tokens": 500}),
    )
    assert report.stop_cause == "token_budget"
    assert det.checked == []
    assert {r.status for r in report.claims} == {"UNVERIFIABLE"}
    assert report.status == "CAUTION"
    assert_consistent(bus, trace)


async def test_timeout_marks_unverified_claims(tmp_path: Path) -> None:
    report, bus, trace = await run(
        tmp_path,
        detectors=[FakeDetector(hang=True)],
        cfg=config(budgets={"timeout_s": 0.05}),
    )
    assert report.stop_cause == "timeout"
    assert {r.status for r in report.claims} == {"UNVERIFIABLE"}
    assert all(v.error for r in report.claims for v in r.verdicts)
    assert report.status == "CAUTION"
    assert any("time limit" in a.message for a in report.alerts)
    assert_consistent(bus, trace)


# --- fail closed ------------------------------------------------------------------------


async def test_detector_crash_is_unverifiable_not_support(tmp_path: Path) -> None:
    report, bus, trace = await run(
        tmp_path, answer="Good one.", detectors=[FakeDetector(crash=True)]
    )
    (result,) = report.claims
    assert result.status == "UNVERIFIABLE"
    assert result.verdicts[0].error and "detector exploded" in result.verdicts[0].error
    assert report.status == "CAUTION"
    assert_consistent(bus, trace)


async def test_forged_evidence_never_supports(tmp_path: Path) -> None:
    report, _, _ = await run(tmp_path, answer="Good one.", detectors=[FakeDetector(forged=True)])
    assert report.claims[0].status != "SUPPORTED"
    assert report.status != "VERIFIED"


async def test_missing_verdict_is_filled_unverifiable(tmp_path: Path) -> None:
    report, _, _ = await run(
        tmp_path, answer="Good one. Good two.", detectors=[FakeDetector(partial=True)]
    )
    statuses = by_text(report)
    assert statuses["Good one."].status == "SUPPORTED"
    assert statuses["Good two."].status == "UNVERIFIABLE"


async def test_decompose_failure_still_completes(tmp_path: Path) -> None:
    report, bus, trace = await run(tmp_path, decompose=FakeDecompose(fail_on=(1,)))
    assert report.status == "CAUTION"
    assert report.final_answer == report.original_draft
    assert report.claims == []
    assert report.alerts == [NOTHING_VERIFIED]
    assert report.stop_cause == "decompose_failed"
    assert_consistent(bus, trace)


async def test_redecompose_failure_after_repair_is_caution(tmp_path: Path) -> None:
    repair = FakeRepair({"Unknown two.": "Good maybe two."})
    report, bus, trace = await run(
        tmp_path,
        answer="Good one. Unknown two.",
        decompose=FakeDecompose(fail_on=(2,)),
        repair=repair,
    )
    assert report.final_answer == "Good one. Good maybe two."
    assert report.status == "CAUTION"
    assert EDITS_UNCHECKED in report.alerts
    assert NOTHING_VERIFIED not in report.alerts
    assert report.stop_cause == "decompose_failed"
    good = by_text(report)["Good one."]
    assert good.claim.span == (0, len("Good one."))
    assert_consistent(bus, trace)


async def test_generate_failure_is_run_failed(tmp_path: Path) -> None:
    async def broken(question: str, ctx: DetectorContext) -> str:
        raise LLMUnavailableError("down", provider=ANTHROPIC, model="claude-haiku-4-5")

    bus = EventBus("r")
    pipeline = Pipeline(make_router(), config(), detectors=[], generate_fn=broken, bus=bus)
    with pytest.raises(LLMUnavailableError):
        await pipeline.run(question="q?")
    assert [e.type for e in bus.history] == ["run.started", "run.failed"]
    assert "down" in bus.history[-1].data["error"]


async def test_cancellation_still_emits_run_failed(tmp_path: Path) -> None:
    bus = EventBus("r")
    pipeline = Pipeline(
        make_router(),
        config(),
        detectors=[FakeDetector(hang=True)],
        decompose_fn=FakeDecompose(),
        bus=bus,
    )
    task = asyncio.create_task(pipeline.run(answer="Good one."))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bus.history[-1].type == "run.failed"
    assert sum(e.type == "run.failed" for e in bus.history) == 1


# --- outcomes ---------------------------------------------------------------------------


async def test_generate_mode_uses_generator_draft(tmp_path: Path) -> None:
    report, bus, _ = await run(tmp_path, answer=None, question="What?")
    assert report.original_draft == "Good one. Good two."
    assert report.status == "VERIFIED"
    assert bus.history[0].data["mode"] == "generate"


async def test_blocked_replaces_answer_with_abstention(tmp_path: Path) -> None:
    report, bus, trace = await run(tmp_path, answer="Bad one. Good two.", cfg=config("fast"))
    assert report.status == "BLOCKED"
    assert report.original_draft == "Bad one. Good two."
    assert "withheld" in report.final_answer and "contradicted" in report.final_answer
    assert any(a.severity == "high" for a in report.alerts)
    assert_consistent(bus, trace)


async def test_fallback_disclosed_and_policy_from_config(tmp_path: Path) -> None:
    det = FakeDetector(fallback=True)
    report, _, _ = await run(tmp_path, answer="Good one.", detectors=[det], cfg=config("strict"))
    assert not report.verifier_diversity
    assert len(report.fallbacks) == 1
    assert det.contexts[0].router.policy is FallbackPolicy.FAIL_CLOSED
    assert any("fell back" in a.message for a in report.alerts)


async def test_alerts_are_emitted_as_events(tmp_path: Path) -> None:
    report, bus, _ = await run(tmp_path, cfg=config("fast"))
    emitted = [e.data["alert"] for e in bus.history if e.type == "alert.raised"]
    assert emitted == [a.model_dump(mode="json") for a in report.alerts]
    assert bus.history[-1].data["status"] == report.status


def test_cost_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = RunUsage()
    usage.add(ANTHROPIC, "claude-opus-5", Usage(input_tokens=1_000_000, output_tokens=0))
    usage.add(VERIFIER, "gemini-3.1-flash-lite", Usage(input_tokens=0, output_tokens=2_000_000))
    assert estimate_cost(usage) == pytest.approx(5.0)
    monkeypatch.setenv("SAIFE_VERIFIER_PRICE_OUT", "0.5")
    assert estimate_cost(usage) == pytest.approx(6.0)


# --- regressions from live runs ---------------------------------------------------------


@pytest.mark.parametrize("same_text", [True, False], ids=["same id", "new id"])
async def test_hedged_claim_is_not_hedged_again(tmp_path: Path, same_text: bool) -> None:
    """smoke/python.json: '12 * 12 = 144' was hedged, then re-decomposed and hedged again."""
    first = "12 multiplied by 12 equals 144."
    second = first if same_text else "12 times 12 is 144."
    decomp = ScriptedDecompose(
        [("Good one.", "Good one.", "entity"), ("12 * 12 = 144", first, "numeric")],
        [
            ("Good one.", "Good one.", "entity"),
            ("12 * 12 = 144 (unverified)", second, "numeric"),
        ],
    )
    repair = ScriptedRepair(
        {
            "12 * 12 = 144": ("hedge", "12 * 12 = 144 (unverified)"),
            "12 * 12 = 144 (unverified)": ("hedge", "12 * 12 may equal 144 (unverified)"),
        }
    )
    report, bus, trace = await run(
        tmp_path,
        answer="Good one. Sum: 12 * 12 = 144.",
        decompose=decomp,
        repair=repair,
        cfg=config(budgets={"max_passes": 4}),
    )
    assert repair.calls == 1
    assert len(report.repairs) == 1
    assert report.final_answer == "Good one. Sum: 12 * 12 = 144 (unverified)."
    assert report.stop_cause == "resolved"
    assert report.passes[1].flagged_ids == []
    assert by_text(report)[second].status == "UNSUPPORTED"  # keeps its status
    assert report.status == "CAUTION"
    assert_consistent(bus, trace)


async def test_disclosed_text_survives_later_edits_before_it(tmp_path: Path) -> None:
    """Disclosed spans shift when a later pass edits text before them."""
    decomp = ScriptedDecompose(
        [("Unknown a", "Unknown a.", "entity"), ("Unknown b", "Unknown b.", "entity")],
        [("Unknown a", "Unknown a.", "entity"), ("maybe b", "Maybe b.", "entity")],
        [("x", "Gone a.", "entity"), ("maybe b", "Maybe b.", "entity")],
    )
    # Pass 0 hedges b only (a gets no edit); pass 1 rewrites a, shifting b's hedge.
    repair = ScriptedRepair({"Unknown b": ("hedge", "maybe b"), "maybe b": ("hedge", "perhaps b")})
    calls: list[int] = []

    async def staged(draft: str, flagged: list[ClaimResult], ctx: DetectorContext) -> RepairOutcome:
        calls.append(len(flagged))
        if ctx.pass_index == 1:
            return await ScriptedRepair({"Unknown a": ("remove", "x")})(draft, flagged, ctx)
        return await repair(draft, flagged, ctx)

    report, _, _ = await run(
        tmp_path,
        answer="Unknown a. Unknown b.",
        decompose=decomp,
        repair=staged,  # type: ignore[arg-type]
        cfg=config(budgets={"max_passes": 4}),
    )
    assert calls == [2, 1]  # pass 1 flags only 'Unknown a'; 'maybe b' was hedged already
    assert report.final_answer == "x. maybe b."
    assert report.passes[2].flagged_ids == [by_text(report)["Gone a."].claim.id]


async def test_claim_rewritten_by_correct_is_not_reported_removed(tmp_path: Path) -> None:
    """smoke/errors.json: a date claim overlapping the corrected span vanished on re-check and
    was alerted as 'could not be verified and was removed'."""
    answer = "It was completed in 1901 for the fair. It is painted gold every year."
    decomp = ScriptedDecompose(
        [
            ("completed in 1901", "Bad completion year 1901.", "date"),
            ("completed in 1901 for the fair", "Unknown completion for the fair.", "date"),
            ("painted gold every year", "Unknown yearly paint.", "date"),
        ],
        [
            ("built between 1887 and 1889", "Good build years.", "date"),
            ("reportedly painted gold every year", "Unknown reported paint.", "date"),
        ],
    )
    repair = ScriptedRepair(
        {
            "completed in 1901": ("correct", "built between 1887 and 1889"),
            "painted gold every year": ("hedge", "reportedly painted gold every year"),
        }
    )
    report, bus, trace = await run(tmp_path, answer=answer, decompose=decomp, repair=repair)
    assert report.final_answer == (
        "It was built between 1887 and 1889 for the fair. It is reportedly painted gold every year."
    )
    assert report.stop_cause == "resolved"
    covered = by_text(report)["Unknown completion for the fair."]
    assert covered.status == "UNSUPPORTED"  # still in the report
    messages = [a.message for a in report.alerts]
    assert not any("removed" in m or "dropped" in m for m in messages)
    assert all(covered.claim.id not in a.claim_ids for a in report.alerts)
    assert any(m.startswith("1 date contradicted the sources and was corrected") for m in messages)
    assert any(m.startswith("1 date could not be confirmed and was reworded") for m in messages)
    assert_consistent(bus, trace)


async def test_vanished_claims_removed_vs_dropped(tmp_path: Path) -> None:
    """A claim deleted with a remove op is 'removed'; one that vanished with no repair
    touching it is 'dropped during re-check'."""
    decomp = ScriptedDecompose(
        [
            ("Unknown a.", "Unknown a.", "numeric"),
            ("Unknown a", "Unknown a figure.", "numeric"),
            ("Unknown q.", "Unknown q.", "date"),
        ],
        [("Unknown q.", "Unknown q, reworded.", "date")],
    )
    repair = ScriptedRepair({"Unknown a.": ("remove", "")})
    report, bus, trace = await run(
        tmp_path, answer="Unknown a. Unknown q.", decompose=decomp, repair=repair
    )
    claims = by_text(report)
    removed = next(a for a in report.alerts if "removed" in a.message)
    assert set(removed.claim_ids) == {
        claims["Unknown a."].claim.id,
        claims["Unknown a figure."].claim.id,
    }
    dropped = next(a for a in report.alerts if "dropped during re-check" in a.message)
    assert dropped.claim_ids == [claims["Unknown q."].claim.id]
    assert dropped.message.startswith("1 date from an earlier draft was dropped")
    assert_consistent(bus, trace)


async def test_surviving_hedge_keeps_answer_at_caution(tmp_path: Path) -> None:
    """A hedge discloses an unverified claim; the answer must not be shown as VERIFIED."""
    repair = FakeRepair({"Unknown one.": "Good maybe one."})  # op="hedge"
    report, bus, trace = await run(tmp_path, answer="Unknown one. Good two.", repair=repair)
    assert report.final_answer == "Good maybe one. Good two."
    assert all(r.status == "SUPPORTED" for r in report.claims if r.claim.text != "Unknown one.")
    assert report.status == "CAUTION"
    assert_consistent(bus, trace)
