"""Invariant 4: no silent edits (P5). Every change between the original draft and the
final answer has a ``RepairRecord``, and undoing the records pass by pass, newest first,
gives back each pass's draft and finally ``original_draft``.

A BLOCKED answer is replaced by an abstention, so there the check starts from the last
draft the loop produced (the last pass's draft, or its repaired text when the run
stopped after repairing).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from saife.types import RepairRecord, Report, Source

from .harness import (
    BUILT,
    FALSE_SENTENCES,
    HEIGHT,
    PARIS,
    SOURCE_TEXT,
    TRUE_SENTENCES,
    Outcome,
    World,
    config,
    invert_pass,
    run_guard,
    seeded,
)

SOURCES = [Source(id="s1", text=SOURCE_TEXT)]
# False sentence -> the source sentence that contradicts it (verified evidence for `correct`).
CONTRADICTS = {
    "It was completed in 1901 for the World's Fair.": BUILT,
    "It is 900 metres tall.": HEIGHT,
    "The tower stands in Lyon.": PARIS,
}


def judge(claim: str, sources: dict[str, str]) -> dict[str, Any]:
    if claim in SOURCE_TEXT:
        return {
            "verdict": "SUPPORTED",
            "confidence": 0.9,
            "quotes": [{"source_id": "s1", "quote": claim}],
        }
    if claim in CONTRADICTS:
        quote = {"source_id": "s1", "quote": CONTRADICTS[claim]}
        return {"verdict": "CONTRADICTED", "confidence": 0.9, "quotes": [quote]}
    return {"verdict": "NOT_FOUND", "confidence": 0.5, "quotes": []}


def last_draft(out: Outcome) -> str:
    """The text the loop ended with, before any abstention replaced it."""
    report = out.report
    assert report is not None
    if report.passes[-1].repairs:
        applied = [e for e in out.events if e.type == "repair.applied"]
        return str(applied[-1].data["draft"])
    return report.passes[-1].draft


def assert_every_change_recorded(out: Outcome) -> None:
    report = out.report
    assert report is not None, repr(out.error)
    current = last_draft(out)
    if report.status != "BLOCKED":
        assert report.final_answer == current
    else:
        assert "withheld" in report.final_answer
    text = current
    for p in reversed(report.passes):
        if p.repairs:
            text = invert_pass(text, p.repairs)
        assert text == p.draft, f"pass {p.index} draft differs from its inverted successor"
    assert text == report.original_draft
    assert_records_consistent(report, out)


def assert_records_consistent(report: Report, out: Outcome) -> None:
    by_pass = [r for p in report.passes for r in p.repairs]
    assert report.repairs == by_pass
    emitted = [
        RepairRecord.model_validate(e.data["repair"])
        for e in out.events
        if e.type == "repair.applied"
    ]
    assert emitted == report.repairs
    for r in report.repairs:
        assert r.pass_index < len(report.passes)
        assert r.claim_id in report.passes[r.pass_index].flagged_ids
        if r.op == "correct":
            assert r.evidence and all(e.verified for e in r.evidence)


def random_repair(rng: Any) -> Any:
    def repair(flagged: list[dict[str, Any]]) -> list[dict[str, Any]]:
        edits: list[dict[str, Any]] = []
        for f in flagged:
            op = rng.choice(["remove", "hedge", "correct", "skip"])
            if op == "skip":
                continue
            replacement = rng.choice(
                [
                    *f["evidence"],  # a valid correction when evidence exists
                    *FALSE_SENTENCES,
                    f"It may be that {f['text']}",
                    "Perhaps it is 12345 metres tall.",  # hedge with a new number: invalid
                    "",
                ]
            )
            edits.append({"claim_id": f["id"], "op": op, "replacement": replacement})
            if rng.random() < 0.2:  # a duplicate or a stray edit
                edits.append({"claim_id": rng.choice([f["id"], "c_000000000000"]), "op": "remove"})
        rng.shuffle(edits)
        return edits

    return repair


SEEDS = list(range(60))


async def _random_run(tmp_path: Path, seed: int) -> Outcome:
    rng = seeded(seed)
    world = World(judge=judge, repair=random_repair(rng))
    pool = TRUE_SENTENCES + FALSE_SENTENCES
    sentences = rng.sample(pool, rng.randint(1, 6))
    sep = rng.choice([" ", "  ", "\n", "\n\n", " \t"])
    preset = rng.choice(["balanced", "strict"])
    cfg = config(
        preset,
        detectors=("grounding",),
        budgets={"max_passes": rng.randint(1, 4)},
        block_on_contradicted=rng.random() < 0.5,
    )
    return await run_guard(tmp_path, world, answer=sep.join(sentences), sources=SOURCES, cfg=cfg)


@pytest.mark.parametrize("seed", SEEDS)
async def test_random_repairs_invert_to_the_original(tmp_path: Path, seed: int) -> None:
    assert_every_change_recorded(await _random_run(tmp_path, seed))


async def test_the_random_runs_cover_blocked_and_repaired(tmp_path: Path) -> None:
    seen: set[tuple[str, bool]] = set()
    ops: set[str] = set()
    for seed in SEEDS:
        out = await _random_run(tmp_path / str(seed), seed)
        assert out.report is not None
        seen.add((out.report.status, bool(out.report.repairs)))
        ops |= {r.op for r in out.report.repairs}
    assert ("BLOCKED", True) in seen and ("VERIFIED", True) in seen and ("CAUTION", True) in seen
    assert ops == {"remove", "hedge", "correct"}


@pytest.mark.parametrize(
    ("draft", "cfg"),
    [
        (f"{PARIS} It is 900 metres tall.", config("balanced", detectors=("grounding",))),
        (f"{PARIS} It is 900 metres tall.", config("fast")),  # BLOCKED, no repair
        (f"It is 900 metres tall.\n\n{PARIS}", config("strict", detectors=("grounding",))),
        (PARIS, config("balanced", detectors=("grounding",))),  # nothing to change
    ],
)
async def test_fixed_scenarios(tmp_path: Path, draft: str, cfg: Any) -> None:
    def correct(flagged: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"claim_id": f["id"], "op": "correct", "replacement": f["evidence"][0]}
            for f in flagged
            if f["evidence"]
        ]

    out = await run_guard(
        tmp_path, World(judge=judge, repair=correct), answer=draft, sources=SOURCES, cfg=cfg
    )
    assert_every_change_recorded(out)


async def test_stopped_after_repair_is_checked_from_the_repaired_draft(tmp_path: Path) -> None:
    """Re-decomposition fails after a repair: the answer is the edited draft, and the
    records still invert it."""
    calls = {"n": 0}

    def claude_error(req: Any) -> str | None:
        if req.schema_name == "DecomposeOut":
            calls["n"] += 1
            return "LLMOutputError" if calls["n"] == 2 else None
        return None

    world = World(judge=judge, claude_error=claude_error)
    out = await run_guard(
        tmp_path,
        world,
        answer=f"{PARIS} The tower is painted gold every year.",
        sources=SOURCES,
        cfg=config("balanced", detectors=("grounding",)),
    )
    assert out.report is not None and out.report.repairs
    assert out.report.final_answer == PARIS
    assert_every_change_recorded(out)
