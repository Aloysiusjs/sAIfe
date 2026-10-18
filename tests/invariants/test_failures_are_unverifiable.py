"""Invariant 2: any detector exception or ``LLMError`` makes the claim UNVERIFIABLE, and
the run emits a ``claim.verdict`` event whose verdict carries the error.

Each scenario isolates one detector, so the failure is the only thing that could
decide the claim.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from saife.config import VERIFIER, FallbackPolicy, GuardConfig
from saife.detectors.base import DetectorContext
from saife.detectors.grounding import GroundingDetector
from saife.events import EventBus
from saife.llm.base import SearchHit
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse
from saife.pipeline.loop import Pipeline
from saife.types import Claim, ClaimResult, DetectorName, DetectorVerdict, Event, Report, Source

from .harness import HEIGHT, PARIS, SOURCE_TEXT, World, config, make_router, run_guard

SOURCES = [Source(id="s1", text=SOURCE_TEXT)]
DRAFT = f"{PARIS} {HEIGHT}"
LLM_ERRORS = ["LLMUnavailableError", "LLMRefusalError", "LLMTruncatedError", "LLMOutputError"]


def only(detector: str, **overrides: Any) -> GuardConfig:
    return config(
        "fast",
        detectors=(detector,),
        fallback_policy=FallbackPolicy.FAIL_CLOSED,
        **overrides,
    )


def last_verdict_events(events: list[Event]) -> dict[str, ClaimResult]:
    out: dict[str, ClaimResult] = {}
    for e in events:
        if e.type == "claim.verdict":
            r = ClaimResult.model_validate(e.data["result"])
            out[r.claim.id] = r
    return out


def assert_failed_closed(
    report: Report, events: list[Event], detector: DetectorName, needle: str
) -> None:
    assert report.claims, "the draft should have produced claims"
    emitted = last_verdict_events(events)
    for r in report.claims:
        assert r.status == "UNVERIFIABLE", r
        assert r.risk > 0
        (v,) = [v for v in r.verdicts if v.detector == detector]
        assert v.verdict == "UNVERIFIABLE" and v.error and needle in v.error, v
        assert emitted[r.claim.id] == r  # the event carries the same result, error included
    assert report.status != "VERIFIED"


@pytest.mark.parametrize("error", LLM_ERRORS)
async def test_grounding_llm_errors(tmp_path: Path, error: str) -> None:
    world = World(verifier_error=lambda req: error)
    out = await run_guard(tmp_path, world, answer=DRAFT, sources=SOURCES, cfg=only("grounding"))
    assert out.report is not None
    assert_failed_closed(out.report, out.events, "grounding", error)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("not json at all", "LLMOutputError"),
        ('{"verdict": "MAYBE", "confidence": 0.9}', "LLMOutputError"),
        ('{"verdict": "SUPPORTED", "confidence": 7}', "LLMOutputError"),
    ],
    ids=["not-json", "bad-verdict", "bad-confidence"],
)
async def test_grounding_malformed_output(tmp_path: Path, text: str, needle: str) -> None:
    world = World()
    verifier = FakeLLM(VERIFIER, handler=lambda req: FakeResponse(text=text))
    router = make_router(world, verifier=verifier)
    out = await run_guard(
        tmp_path, world, answer=DRAFT, sources=SOURCES, cfg=only("grounding"), router=router
    )
    assert out.report is not None
    assert_failed_closed(out.report, out.events, "grounding", needle)


async def test_fallback_that_also_fails(tmp_path: Path) -> None:
    """Verifier outage, fallback allowed, and the fallback model refuses too."""

    def claude_error(req: FakeRequest) -> str | None:
        return "LLMRefusalError" if req.schema_name == "GroundingJudgement" else None

    world = World(verifier_error=lambda req: "LLMUnavailableError", claude_error=claude_error)
    cfg = config(
        "fast", detectors=("grounding",), fallback_policy=FallbackPolicy.FALLBACK_AND_DISCLOSE
    )
    out = await run_guard(tmp_path, world, answer=DRAFT, sources=SOURCES, cfg=cfg)
    assert out.report is not None
    assert_failed_closed(out.report, out.events, "grounding", "LLMRefusalError")


def _search_fails(req: FakeRequest) -> str | None:
    return "LLMUnavailableError" if req.kind == "search" else None


def _web_judge_fails(req: FakeRequest) -> str | None:
    return "LLMTruncatedError" if req.schema_name == "WebJudgement" else None


@pytest.mark.parametrize(
    ("claude_error", "verifier_error", "needle"),
    [
        (_search_fails, None, "web search failed"),
        (None, _web_judge_fails, "web judge failed"),
    ],
    ids=["search", "judge"],
)
async def test_web_evidence_errors(
    tmp_path: Path,
    claude_error: Callable[[FakeRequest], str | None] | None,
    verifier_error: Callable[[FakeRequest], str | None] | None,
    needle: str,
) -> None:
    world = World(search=lambda claim: [SearchHit(url="https://example.org", text=SOURCE_TEXT)])
    if claude_error:
        world.claude_error = claude_error
    if verifier_error:
        world.verifier_error = verifier_error
    cfg = config("strict", detectors=("web_evidence",), repair=False)
    out = await run_guard(tmp_path, world, answer=DRAFT, cfg=cfg)
    assert out.report is not None
    assert_failed_closed(out.report, out.events, "web_evidence", needle)


@pytest.mark.parametrize("error", ["LLMUnavailableError", "LLMRefusalError"])
async def test_consistency_sampling_errors(tmp_path: Path, error: str) -> None:
    world = World(claude_error=lambda req: error if req.kind == "complete" else None)
    out = await run_guard(
        tmp_path,
        world,
        answer=DRAFT,
        question="How tall is it?",
        cfg=only("consistency", consistency_samples=3),
    )
    assert out.report is not None
    assert_failed_closed(out.report, out.events, "consistency", error)


@pytest.mark.parametrize("error", ["LLMOutputError", "LLMUnavailableError"])
async def test_consistency_judge_errors(tmp_path: Path, error: str) -> None:
    world = World(verifier_error=lambda req: error)
    out = await run_guard(
        tmp_path,
        world,
        answer=DRAFT,
        question="How tall is it?",
        cfg=only("consistency", consistency_samples=3),
    )
    assert out.report is not None
    assert_failed_closed(out.report, out.events, "consistency", error)


# --- arbitrary exceptions in a detector, through the real Pipeline -------------------------


class Broken:
    """A detector that misbehaves in a chosen way."""

    name: DetectorName = "deterministic"

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def applies(self, ctx: DetectorContext) -> bool:
        if self.mode == "applies":
            raise ValueError("applies exploded")
        return True

    async def check(self, claims: list[Claim], ctx: DetectorContext) -> list[DetectorVerdict]:
        if self.mode == "raise":
            raise RuntimeError("check exploded")
        if self.mode == "timeout":
            raise TimeoutError("inner timeout")
        if self.mode == "keyerror":
            return [{}[c.id] for c in claims]
        if self.mode == "none":
            return None  # type: ignore[return-value]
        if self.mode == "junk":
            return ["not a verdict"]  # type: ignore[list-item]
        raise AssertionError(self.mode)


@pytest.mark.parametrize(
    ("mode", "needle"),
    [
        ("applies", "applies exploded"),
        ("raise", "check exploded"),
        ("timeout", "inner timeout"),
        ("keyerror", "KeyError"),
        ("none", "deterministic"),
        ("junk", "deterministic"),
    ],
)
async def test_detector_exceptions(mode: str, needle: str) -> None:
    world = World()
    bus = EventBus("r")
    pipeline = Pipeline(
        make_router(world),
        config("fast", detectors=("deterministic",)),
        detectors=[Broken(mode), GroundingDetector()],
        bus=bus,
    )
    world.verifier_error = lambda req: "LLMUnavailableError"  # grounding fails too
    report = await pipeline.run(answer=DRAFT, sources=SOURCES)
    assert_failed_closed(report, bus.history, "deterministic", needle)
    assert_failed_closed(report, bus.history, "grounding", "LLMUnavailableError")


async def test_one_failing_detector_never_reads_as_support(tmp_path: Path) -> None:
    """A crash next to an honest NOT_FOUND still leaves the claim unsupported, never
    SUPPORTED; next to verified support, the crash is recorded on the verdict."""
    world = World()
    bus = EventBus("r")
    pipeline = Pipeline(
        make_router(world),
        config("fast", detectors=("grounding",)),
        detectors=[Broken("raise"), GroundingDetector()],
        bus=bus,
    )
    report = await pipeline.run(answer=f"{PARIS} It is 900 metres tall.", sources=SOURCES)
    by_text = {r.claim.text: r for r in report.claims}
    assert by_text[PARIS].status == "SUPPORTED"
    assert by_text["It is 900 metres tall."].status == "UNSUPPORTED"
    for r in report.claims:
        (v,) = [v for v in r.verdicts if v.detector == "deterministic"]
        assert v.verdict == "UNVERIFIABLE" and v.error and "check exploded" in v.error
