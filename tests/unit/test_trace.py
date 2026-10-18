from __future__ import annotations

import json
from pathlib import Path

import pytest

from saife.errors import SaifeError
from saife.events import EventBus
from saife.redact import REDACTED
from saife.trace import TraceSink, load_trace, replay, verify_trace
from saife.types import (
    Alert,
    Claim,
    ClaimResult,
    DetectorVerdict,
    Evidence,
    PassRecord,
    RepairRecord,
    Report,
    RunUsage,
)
from tests.conftest import FAKE_ANTHROPIC_KEY

DRAFT = "Paris is the capital of France. It has 90 million people."
FINAL = "Paris is the capital of France."


def _report(run_id: str = "r1") -> tuple[Report, list[ClaimResult], RepairRecord]:
    c1 = Claim.make("Paris is the capital of France.", anchor="Paris", span=(0, 5), kind="entity")
    c2 = Claim.make(
        "Paris has 90 million people.", anchor="90 million", span=(43, 53), kind="numeric"
    )
    ev = Evidence(source_id="s1", quote="Paris is the capital of France", verified=True)
    r1 = ClaimResult(
        claim=c1,
        status="SUPPORTED",
        risk=0.05,
        verdicts=[
            DetectorVerdict(
                detector="grounding",
                claim_id=c1.id,
                verdict="SUPPORTED",
                confidence=0.9,
                evidence=[ev],
            )
        ],
    )
    r2 = ClaimResult(claim=c2, status="UNSUPPORTED", risk=0.7)
    repair = RepairRecord(
        claim_id=c2.id,
        op="remove",
        before=" It has 90 million people.",
        after="",
        span=(31, 57),
        reason="no evidence",
        pass_index=0,
    )
    report = Report(
        run_id=run_id,
        status="CAUTION",
        preset="balanced",
        question="What is the capital of France?",
        original_draft=DRAFT,
        final_answer=FINAL,
        claims=[r1, r2],
        passes=[
            PassRecord(
                index=0,
                draft=DRAFT,
                claim_ids=[c1.id, c2.id],
                verified_ids=[c1.id, c2.id],
                flagged_ids=[c2.id],
                repairs=[repair],
            )
        ],
        repairs=[repair],
        alerts=[Alert(severity="warning", claim_ids=[c2.id], message="Removed a claim.")],
        usage=RunUsage(input_tokens=10, output_tokens=5, calls=2),
        risk=0.4,
        stop_cause="resolved",
    )
    return report, [r1, r2], repair


async def _run(path: Path, *, complete: bool = True, secret_in: str | None = None) -> Report:
    report, results, repair = _report()
    bus = EventBus(report.run_id)
    bus.add_sink(TraceSink(path))
    await bus.emit(
        "run.started",
        question=secret_in or report.question,
        preset="balanced",
        mode="generate",
        sources=["s1"],
    )
    await bus.emit("pass.started", index=0, draft=DRAFT)
    for r in results:
        await bus.emit("claim.extracted", pass_index=0, claim=r.claim)
    # An earlier, superseded verdict for c1: the last one must win.
    await bus.emit(
        "claim.verdict", pass_index=0, result=results[0].model_copy(update={"status": "LIKELY"})
    )
    for r in results:
        await bus.emit("claim.verdict", pass_index=0, result=r)
    await bus.emit("repair.applied", pass_index=0, repair=repair, draft=FINAL)
    await bus.emit("alert.raised", alert=report.alerts[0])
    if complete:
        await bus.emit("run.completed", status=report.status, report=report)
    return report


async def test_round_trip_replay_equals_report(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "nested" / "r1.jsonl"
    report = await _run(path)
    assert path.exists()
    events = load_trace(path)
    assert [e.seq for e in events] == list(range(len(events)))
    assert verify_trace(path) == []
    assert replay(path) == report


async def test_each_event_is_one_line(tmp_path: Path) -> None:
    path = tmp_path / "r1.jsonl"
    await _run(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(load_trace(path))
    assert all(json.loads(line)["run_id"] == "r1" for line in lines)


async def test_secret_in_event_data_is_redacted(tmp_path: Path) -> None:
    path = tmp_path / "r1.jsonl"
    await _run(path, secret_in=f"my key is {FAKE_ANTHROPIC_KEY}")
    text = path.read_text(encoding="utf-8")
    assert FAKE_ANTHROPIC_KEY not in text
    assert REDACTED in text
    load_trace(path)  # still valid JSON


async def test_incomplete_trace_raises(tmp_path: Path) -> None:
    path = tmp_path / "r1.jsonl"
    await _run(path, complete=False)
    with pytest.raises(SaifeError, match="run did not complete"):
        replay(path)


async def test_failed_run_raises(tmp_path: Path) -> None:
    path = tmp_path / "r1.jsonl"
    bus = EventBus("r1")
    bus.add_sink(TraceSink(path))
    await bus.emit("run.failed", error="budget blown")
    with pytest.raises(SaifeError, match="budget blown"):
        replay(path)


def _rewrite(path: Path, fn: object) -> None:
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert callable(fn)
    lines = fn(lines)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


async def test_tampered_verdict_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "r1.jsonl"
    await _run(path)

    def tamper(lines: list[dict[str, object]]) -> list[dict[str, object]]:
        last_verdict = max(i for i, x in enumerate(lines) if x["type"] == "claim.verdict")
        data = lines[last_verdict]["data"]
        assert isinstance(data, dict)
        data["result"]["status"] = "SUPPORTED"
        return lines

    _rewrite(path, tamper)
    problems = verify_trace(path)
    assert len(problems) == 1 and "differs from the report" in problems[0]
    with pytest.raises(SaifeError, match="does not match"):
        replay(path)


async def test_missing_repair_event_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "r1.jsonl"
    await _run(path)
    _rewrite(path, lambda ls: [x for x in ls if x["type"] != "repair.applied"])
    problems = verify_trace(path)
    assert any("repair.applied" in p for p in problems)
    assert any("seq" in p for p in problems)


def test_invalid_line_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"type": "nope"}\n', encoding="utf-8")
    with pytest.raises(SaifeError, match="bad.jsonl:1"):
        load_trace(path)
