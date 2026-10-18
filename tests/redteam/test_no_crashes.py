"""Sweep: every payload in every location under every preset, with an obedient verifier.
Zero crashes: each run completes with a report and exactly one terminal event, and no
injected or false claim ends SUPPORTED.

The consistency judge obeys too (every sample "supports"). Agreement is not evidence, so
that can lift a claim to LIKELY at most; by design (PRD §4.3) an answer whose claims are
all LIKELY is VERIFIED, which this sweep records rather than forbids.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from saife.llm.base import SearchHit
from saife.types import Source
from tests.invariants.harness import SOURCE_TEXT, World, assert_one_terminal, run_guard

from .payloads import FALSE_CLAIM, INJECTIONS, obedient_judge, obedient_web_judge

LOCATIONS = ["source", "draft", "search", "generated", "question"]
PRESETS = ["fast", "balanced", "strict"]


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("location", LOCATIONS)
async def test_sweep_has_no_crashes(tmp_path: Path, location: str, preset: str) -> None:
    runs = 0
    for i, (name, payload) in enumerate(INJECTIONS.items()):
        source_text = SOURCE_TEXT + (f"\n\n{payload}" if location == "source" else "")
        hit_text = payload if location == "search" else "The tower has three visitor levels."
        world = World(
            judge=obedient_judge,
            web_judge=obedient_web_judge,
            search=lambda claim, t=hit_text: [SearchHit(url="https://x.example/", text=t)],
            generated=f"{FALSE_CLAIM} {payload}" if location == "generated" else FALSE_CLAIM,
            consistency=lambda claim, ids: {
                "verdicts": [{"sample_id": s, "verdict": "supports"} for s in ids]
            },
        )
        draft = f"{FALSE_CLAIM} {payload}" if location == "draft" else FALSE_CLAIM
        question = payload if location == "question" else "How tall is the Eiffel Tower?"
        out = await run_guard(
            tmp_path / f"{i}-{name}",
            world,
            answer=None if location in ("generated", "question") else draft,
            question=question,
            sources=[Source(id="s1", text=source_text)],
            cfg=preset,
        )
        assert out.error is None, f"{name}: {out.error!r}"
        report = out.report
        assert report is not None
        assert_one_terminal(out.events)
        assert out.events[-1].type == "run.completed"
        assert all(r.status != "SUPPORTED" for r in report.claims), (name, report.claims)
        if report.status == "VERIFIED":
            assert {r.status for r in report.claims} == {"LIKELY"}, (name, report.claims)
        runs += 1
    assert runs == len(INJECTIONS)
