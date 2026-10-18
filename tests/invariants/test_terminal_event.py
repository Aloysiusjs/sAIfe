"""Invariant 5: every run ends with exactly one ``run.completed`` or ``run.failed`` event,
and it is the last event, both on the bus and in the JSONL trace.

Covers clean runs, failures the loop absorbs (decompose, detector, repair, sinks) and
failures that end the run (generation, malformed collaborator output, cancellation).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from saife.detectors.base import DetectorContext
from saife.detectors.grounding import GroundingDetector
from saife.events import EventBus
from saife.pipeline.decompose import decompose
from saife.pipeline.loop import Pipeline
from saife.pipeline.repair import repair
from saife.trace import TraceSink, load_trace
from saife.types import Claim, ClaimResult, DetectorName, DetectorVerdict, Event, Source

from .harness import PARIS, SOURCE_TEXT, World, assert_one_terminal, config, make_router, run_guard

SOURCES = [Source(id="s1", text=SOURCE_TEXT)]
DRAFT = f"{PARIS} It is 900 metres tall."


async def _ok_generate(question: str, ctx: DetectorContext) -> str:
    return DRAFT


async def _raise(*args: Any) -> Any:
    raise RuntimeError("unexpected bug")


async def _garbage_claims(draft: str, ctx: DetectorContext) -> list[Claim]:
    return ["not a claim"]  # type: ignore[list-item]


async def _none_repair(draft: str, flagged: list[ClaimResult], ctx: DetectorContext) -> Any:
    return None


async def _hang(*args: Any) -> Any:
    await asyncio.sleep(3600)


class Crashing:
    name: DetectorName = "deterministic"

    def __init__(self, where: str) -> None:
        self.where = where

    def applies(self, ctx: DetectorContext) -> bool:
        if self.where == "applies":
            raise ZeroDivisionError("applies")
        return True

    async def check(self, claims: list[Claim], ctx: DetectorContext) -> list[DetectorVerdict]:
        raise MemoryError("check") if self.where == "memory" else KeyError("check")


async def _pipeline_run(
    tmp_path: Path,
    *,
    answer: str | None = DRAFT,
    detectors: list[Any] | None = None,
    decompose_fn: Callable[..., Awaitable[Any]] = decompose,
    repair_fn: Callable[..., Awaitable[Any]] = repair,
    generate_fn: Callable[..., Awaitable[Any]] = _ok_generate,
    sinks: list[Any] | None = None,
) -> tuple[list[Event], list[Event], BaseException | None]:
    world = World()
    bus = EventBus("term")
    trace = tmp_path / "term.jsonl"
    bus.add_sink(TraceSink(trace))
    for s in sinks or []:
        bus.add_sink(s)
    pipeline = Pipeline(
        make_router(world),
        config("balanced", detectors=("grounding",)),
        detectors=detectors if detectors is not None else [GroundingDetector()],
        decompose_fn=decompose_fn,
        repair_fn=repair_fn,
        generate_fn=generate_fn,
        bus=bus,
    )
    error: BaseException | None = None
    try:
        await pipeline.run(answer=answer, question="q?", sources=SOURCES)
    except Exception as exc:
        error = exc
    await bus.drain()
    return list(bus.history), load_trace(trace), error


def assert_terminal(history: list[Event], trace: list[Event], expected: str) -> None:
    assert_one_terminal(history)
    assert history[-1].type == expected
    assert [e.type for e in trace] == [e.type for e in history]


@pytest.mark.parametrize(
    ("kwargs", "expected", "raises"),
    [
        ({}, "run.completed", None),
        ({"answer": None}, "run.completed", None),
        ({"decompose_fn": _raise}, "run.completed", None),
        ({"repair_fn": _raise}, "run.completed", None),
        ({"detectors": [Crashing("applies")]}, "run.completed", None),
        ({"detectors": [Crashing("check")]}, "run.completed", None),
        ({"detectors": [Crashing("memory"), GroundingDetector()]}, "run.completed", None),
        ({"answer": None, "generate_fn": _raise}, "run.failed", RuntimeError),
        ({"decompose_fn": _garbage_claims}, "run.failed", AttributeError),
        ({"repair_fn": _none_repair}, "run.failed", AttributeError),
    ],
    ids=[
        "clean",
        "generate-mode",
        "decompose-bug",
        "repair-bug",
        "applies-bug",
        "check-bug",
        "check-memoryerror",
        "generate-bug",
        "garbage-claims",
        "repair-returns-none",
    ],
)
async def test_exactly_one_terminal_event(
    tmp_path: Path, kwargs: dict[str, Any], expected: str, raises: type[Exception] | None
) -> None:
    history, trace, error = await _pipeline_run(tmp_path, **kwargs)
    assert_terminal(history, trace, expected)
    if raises is None:
        assert error is None, repr(error)
    else:
        assert isinstance(error, raises)
        assert type(error).__name__ in history[-1].data["error"]


async def test_failing_sink_does_not_change_the_ending(tmp_path: Path) -> None:
    async def bad_sink(event: Event) -> None:
        raise OSError("disk full")

    history, trace, error = await _pipeline_run(tmp_path, sinks=[bad_sink])
    assert error is None
    assert_terminal(history, trace, "run.completed")


@pytest.mark.parametrize("where", ["generate", "decompose", "repair"])
async def test_cancellation_ends_with_run_failed(tmp_path: Path, where: str) -> None:
    bus = EventBus("cancel")
    world = World()
    pipeline = Pipeline(
        make_router(world),
        config("balanced", detectors=("grounding",)),
        detectors=[GroundingDetector()],
        generate_fn=_hang if where == "generate" else _ok_generate,
        decompose_fn=_hang if where == "decompose" else decompose,
        repair_fn=_hang if where == "repair" else repair,
        bus=bus,
    )
    task = asyncio.create_task(pipeline.run(question="q?", sources=SOURCES))
    for _ in range(200):
        await asyncio.sleep(0.005)
        if where != "repair" or any(e.type == "claim.verdict" for e in bus.history):
            break
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_one_terminal(bus.history)
    assert bus.history[-1].type == "run.failed"


@pytest.mark.parametrize(
    "claude_error",
    [
        lambda req: "LLMUnavailableError" if req.kind == "complete" else None,
        lambda req: "LLMRefusalError" if req.schema_name == "DecomposeOut" else None,
        lambda req: "LLMOutputError" if req.schema_name == "RepairOut" else None,
        lambda req: None,
    ],
    ids=["generate", "decompose", "repair", "clean"],
)
async def test_guard_trace_has_one_terminal_event(tmp_path: Path, claude_error: Any) -> None:
    world = World(claude_error=claude_error)
    out = await run_guard(tmp_path, world, question="Tell me about it.", sources=SOURCES)
    assert_one_terminal(out.events)
    assert [e.type for e in out.trace] == [e.type for e in out.events]
    assert (out.error is None) == (out.events[-1].type == "run.completed")


async def test_bad_arguments_start_no_run(tmp_path: Path) -> None:
    """Rejected before ``run.started``: there is no run, so no events at all."""
    bus = EventBus("none")
    pipeline = Pipeline(make_router(World()), config(), detectors=[], bus=bus)
    with pytest.raises(ValueError):
        await pipeline.run()
    assert bus.history == []
