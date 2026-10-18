"""Invariant 3: every run terminates within its budget (P4).

- the pass cap: never more passes, decompositions or repairs than ``max_passes`` allows;
- the token budget: once spent, no new stage starts, so the overshoot is at most one
  stage's worth of calls, and whatever was not verified is UNVERIFIABLE;
- the timeout: a model that hangs is cut off at ``timeout_s`` and the run still ends.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from saife.config import ANTHROPIC, VERIFIER
from saife.llm.base import Usage
from saife.llm.fake import FakeLLM
from saife.types import Source

from .harness import (
    FALSE_SENTENCES,
    PARIS,
    SOURCE_TEXT,
    TRUE_SENTENCES,
    SlowFakeLLM,
    World,
    assert_one_terminal,
    config,
    make_router,
    run_guard,
    seeded,
)

SOURCES = [Source(id="s1", text=SOURCE_TEXT)]


def remove_first(flagged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fix one flagged claim per pass, so the flagged count strictly shrinks."""
    return [{"claim_id": flagged[0]["id"], "op": "remove"}] if flagged else []


def counting(world: World) -> tuple[FakeLLM, FakeLLM]:
    return FakeLLM(ANTHROPIC, handler=world.claude), FakeLLM(VERIFIER, handler=world.verifier)


@pytest.mark.parametrize("max_passes", [1, 2, 3, 4, 5])
async def test_pass_cap_is_respected(tmp_path: Path, max_passes: int) -> None:
    world = World(repair=remove_first)
    claude, verifier = counting(world)
    draft = " ".join([PARIS, *FALSE_SENTENCES[:4]])  # 4 flagged → needs 5 passes to resolve
    out = await run_guard(
        tmp_path,
        world,
        answer=draft,
        sources=SOURCES,
        cfg=config("balanced", budgets={"max_passes": max_passes}),
        router=make_router(world, claude=claude, verifier=verifier),
    )
    report = out.report
    assert report is not None
    decomposes = [c for c in claude.calls if c.schema_name == "DecomposeOut"]
    repairs = [c for c in claude.calls if c.schema_name == "RepairOut"]
    assert len(report.passes) <= max_passes
    assert len(decomposes) <= max_passes and len(repairs) <= max_passes - 1
    expected = "resolved" if max_passes >= 5 else "max_passes"
    assert report.stop_cause == expected
    assert_one_terminal(out.events)


@pytest.mark.parametrize("seed", range(20))
async def test_random_repairs_never_exceed_the_pass_cap(tmp_path: Path, seed: int) -> None:
    rng = seeded(seed)

    def repair(flagged: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ops = ["remove", "hedge", "correct"]
        return [
            {
                "claim_id": f["id"],
                "op": rng.choice(ops),
                "replacement": rng.choice([*FALSE_SENTENCES, f"It may be that {f['text']}"]),
            }
            for f in flagged
            if rng.random() < 0.7
        ]

    world = World(repair=repair)
    claude, _ = counting(world)
    max_passes = rng.randint(1, 6)
    draft = " ".join(rng.sample(TRUE_SENTENCES + FALSE_SENTENCES, rng.randint(1, 6)))
    out = await run_guard(
        tmp_path,
        world,
        answer=draft,
        sources=SOURCES,
        cfg=config("balanced", budgets={"max_passes": max_passes}),
        router=make_router(world, claude=claude),
    )
    assert out.report is not None
    assert len(out.report.passes) <= max_passes
    assert sum(c.schema_name == "DecomposeOut" for c in claude.calls) <= max_passes
    assert_one_terminal(out.events)


PER_CALL = 100


@pytest.mark.parametrize("max_tokens", [1, 99, 100, 150, 250, 400, 450, 1_000, 10_000])
async def test_token_budget_is_respected(tmp_path: Path, max_tokens: int) -> None:
    world = World(repair=remove_first, usage=Usage(input_tokens=PER_CALL - 10, output_tokens=10))
    claude, verifier = counting(world)
    draft = " ".join([PARIS, *FALSE_SENTENCES[:2]])  # 3 claims
    cfg = config(
        "balanced",
        detectors=("grounding",),
        budgets={"max_tokens": max_tokens, "max_passes": 4},
    )
    out = await run_guard(
        tmp_path,
        world,
        answer=draft,
        sources=SOURCES,
        cfg=cfg,
        router=make_router(world, claude=claude, verifier=verifier),
    )
    report = out.report
    assert report is not None
    calls = len(claude.calls) + len(verifier.calls)
    assert report.usage.total_tokens == calls * PER_CALL  # nothing escapes accounting
    stage = 3 * PER_CALL  # the biggest stage: one grounding call per claim
    assert report.usage.total_tokens < max_tokens + stage
    if report.usage.total_tokens >= max_tokens:
        assert report.stop_cause == "token_budget"
        assert report.status != "VERIFIED"
        never_checked = [r for r in report.claims if not any(v.model for v in r.verdicts)]
        assert all(r.status == "UNVERIFIABLE" for r in never_checked)
    assert_one_terminal(out.events)


async def test_tiny_token_budget_still_reports(tmp_path: Path) -> None:
    world = World(usage=Usage(input_tokens=5_000))
    cfg = config("balanced", budgets={"max_tokens": 1})
    out = await run_guard(tmp_path, world, answer=PARIS, sources=SOURCES, cfg=cfg)
    report = out.report
    assert report is not None
    assert report.stop_cause == "token_budget"
    assert {r.status for r in report.claims} == {"UNVERIFIABLE"}
    assert report.status == "CAUTION"
    assert any(v.error and "token budget" in v.error for r in report.claims for v in r.verdicts)


TIMEOUT = 0.2
SLACK = 2.0  # generous: a hang would take DELAY
DELAY = 30.0


@pytest.mark.parametrize(
    ("slow_on", "mode"),
    [
        ("GroundingJudgement", "answer"),
        ("DecomposeOut", "answer"),
        ("RepairOut", "answer"),
        ("complete", "answer"),  # consistency sampling
        ("complete", "generate"),  # the generator itself
    ],
)
async def test_timeout_is_respected(tmp_path: Path, slow_on: str, mode: str) -> None:
    world = World()
    claude = SlowFakeLLM(
        ANTHROPIC, delay=DELAY, slow=lambda name: name == slow_on, handler=world.claude
    )
    verifier = SlowFakeLLM(
        VERIFIER, delay=DELAY, slow=lambda name: name == slow_on, handler=world.verifier
    )
    cfg = config("balanced", budgets={"timeout_s": TIMEOUT, "max_passes": 3})
    t0 = time.monotonic()
    out = await run_guard(
        tmp_path,
        world,
        answer=f"{PARIS} It is 900 metres tall." if mode == "answer" else None,
        question="Tell me about the Eiffel Tower.",
        sources=SOURCES,
        cfg=cfg,
        router=make_router(world, claude=claude, verifier=verifier),
    )
    elapsed = time.monotonic() - t0
    assert elapsed < TIMEOUT + SLACK
    assert_one_terminal(out.events)
    if mode == "generate":  # nothing to report: the run fails, loudly and promptly
        assert isinstance(out.error, TimeoutError)
        assert out.events[-1].type == "run.failed"
        return
    report = out.report
    assert report is not None, repr(out.error)
    assert report.stop_cause == "timeout"
    assert report.status == "CAUTION"
    assert report.usage.wall_time_s < TIMEOUT + SLACK
    assert all(r.status != "SUPPORTED" or r.pass_index == 0 for r in report.claims)
    assert any("time limit" in a.message for a in report.alerts)
