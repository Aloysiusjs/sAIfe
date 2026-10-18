"""JSONL run traces and offline replay (PRD P8).

Every event becomes one redacted JSON line, flushed as it is written, so a crash
still leaves a readable trace and no key can land on disk (N4). ``replay`` rebuilds
the ``Report`` with no LLM calls and cross-checks it against the per-claim and
per-repair events, so an edited or truncated trace is caught rather than trusted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from saife.errors import SaifeError
from saife.events import TERMINAL
from saife.redact import redact
from saife.types import ClaimResult, Event, RepairRecord, Report


class TraceSink:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def __call__(self, event: Event) -> None:
        self._append(redact(event.model_dump_json()))

    def _append(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()


def load_trace(path: Path) -> list[Event]:
    events: list[Event] = []
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                events.append(Event.model_validate_json(line))
            except ValidationError as exc:
                raise SaifeError(f"{path}:{lineno}: invalid trace line: {exc}") from exc
    return events


def _terminal(events: list[Event]) -> Event:
    if not events:
        raise SaifeError("trace is empty: run did not complete")
    last = events[-1]
    if last.type not in TERMINAL:
        raise SaifeError("run did not complete: trace has no run.completed or run.failed event")
    if last.type == "run.failed":
        raise SaifeError(f"run failed: {last.data.get('error', 'unknown error')}")
    return last


def _report_from(event: Event) -> Report:
    raw = event.data.get("report")
    if raw is None:
        raise SaifeError("run.completed event carries no report")
    try:
        return Report.model_validate(raw)
    except ValidationError as exc:
        raise SaifeError(f"run.completed report is invalid: {exc}") from exc


def _dump(model: ClaimResult | RepairRecord) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _check(events: list[Event], report: Report) -> list[str]:
    problems: list[str] = []
    run_ids = {e.run_id for e in events}
    if run_ids != {report.run_id}:
        problems.append(f"run ids {sorted(run_ids)} do not match report {report.run_id!r}")
    seqs = [e.seq for e in events]
    if seqs != list(range(len(events))):
        problems.append("event seq is not contiguous from 0")
    if sum(e.type in TERMINAL for e in events) != 1:
        problems.append("trace must have exactly one terminal event")
    status = events[-1].data.get("status")
    if status != report.status:
        problems.append(f"run.completed status {status!r} != report status {report.status!r}")

    # Last verdict per claim wins: claims re-verified in a later pass replace earlier results.
    latest: dict[str, ClaimResult] = {}
    repairs: list[RepairRecord] = []
    for e in events:
        try:
            if e.type == "claim.verdict":
                result = ClaimResult.model_validate(e.data.get("result"))
                latest[result.claim.id] = result
            elif e.type == "repair.applied":
                repairs.append(RepairRecord.model_validate(e.data.get("repair")))
        except ValidationError:
            problems.append(f"event #{e.seq} ({e.type}) has an invalid payload")

    for claim in report.claims:
        cid = claim.claim.id
        if cid not in latest:
            problems.append(f"claim {cid} is in the report but has no claim.verdict event")
        elif _dump(latest[cid]) != _dump(claim):
            problems.append(f"claim {cid}: last claim.verdict event differs from the report")

    if len(repairs) != len(report.repairs):
        problems.append(
            f"{len(repairs)} repair.applied events but {len(report.repairs)} repairs in report"
        )
    for i, (seen, reported) in enumerate(zip(repairs, report.repairs, strict=False)):
        if _dump(seen) != _dump(reported):
            problems.append(f"repair {i} ({reported.claim_id}) differs from its event")
    return problems


def verify_trace(path: Path) -> list[str]:
    """Mismatches between the trace's events and its final report (empty = consistent)."""
    events = load_trace(path)
    report = _report_from(_terminal(events))
    return _check(events, report)


def replay(path: Path) -> Report:
    """Rebuild the report from a trace without any model calls.

    Raises ``SaifeError`` if the run did not complete or the events disagree with
    the stored report.
    """
    events = load_trace(path)
    report = _report_from(_terminal(events))
    problems = _check(events, report)
    if problems:
        raise SaifeError("trace does not match its report: " + "; ".join(problems))
    return report
