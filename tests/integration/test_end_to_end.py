"""End to end through the public ``Guard`` API with the real pipeline modules and
detectors; only the models (FakeLLM handlers), HTTP (MockTransport) and DNS are faked."""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

from saife import Guard, Report, Source, get_preset
from saife.config import ANTHROPIC, VERIFIER, FallbackPolicy, GuardConfig, default_roles
from saife.llm.base import Usage
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse
from saife.llm.router import Router
from saife.trace import load_trace, replay
from saife.types import Event, claim_id

EIFFEL = (
    "The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars in Paris, France. "
    "It was designed and built by the company of engineer Gustave Eiffel between 1887 and 1889 "
    "as the centrepiece of the 1889 World's Fair. The tower is 330 metres tall, including its "
    "antennas."
)
SOURCES = [Source(id="eiffel", text=EIFFEL, title="eiffel.md")]

S_PARIS = "The Eiffel Tower stands on the Champ de Mars in Paris."
S_1901 = "It was completed in 1901 for the World's Fair."
S_FIXED = "It was built between 1887 and 1889 for the World's Fair."
S_HEIGHT = "The tower is 330 metres tall, including its antennas."
S_GOLD = "It is painted gold every year."

# sentence -> (claim text, grounding verdict, quote)
CLAIMS: dict[str, tuple[str, str, str]] = {
    S_PARIS: (
        "The Eiffel Tower is on the Champ de Mars in Paris.",
        "SUPPORTED",
        "a wrought-iron lattice tower on the Champ de Mars in Paris",
    ),
    S_1901: (
        "The Eiffel Tower was completed in 1901.",
        "CONTRADICTED",
        "built by the company of engineer Gustave Eiffel between 1887 and 1889",
    ),
    S_FIXED: (
        "The Eiffel Tower was built between 1887 and 1889.",
        "SUPPORTED",
        "built by the company of engineer Gustave Eiffel between 1887 and 1889",
    ),
    S_HEIGHT: (
        "The Eiffel Tower is 330 metres tall including antennas.",
        "SUPPORTED",
        "The tower is 330 metres tall, including its antennas",
    ),
    S_GOLD: ("The Eiffel Tower is painted gold every year.", "NOT_FOUND", ""),
}
TEXT_TO_SENTENCE = {text: s for s, (text, _, _) in CLAIMS.items()}


def _user(req: FakeRequest) -> str:
    return html.unescape(str(req.messages[-1]["content"]))


def _decompose(req: FakeRequest) -> FakeResponse:
    content = _user(req)
    draft = content.split("<draft>", 1)[1].split("</draft>", 1)[0]
    claims = [
        {"text": CLAIMS[s][0], "anchor": s, "kind": "entity", "checkworthy": True}
        for s in sorted((s for s in CLAIMS if s in draft), key=draft.index)
    ]
    return FakeResponse(text=json.dumps({"claims": claims}))


def _ground(req: FakeRequest) -> FakeResponse:
    content = _user(req)
    claim = content.split("<claim>", 1)[1].split("</claim>", 1)[0].strip()
    _, verdict, quote = CLAIMS[TEXT_TO_SENTENCE[claim]]
    quotes = [{"source_id": "eiffel", "quote": quote}] if quote else []
    return FakeResponse(
        text=json.dumps({"verdict": verdict, "confidence": 0.9, "quotes": quotes}),
        usage=Usage(input_tokens=100, output_tokens=20),
    )


def _repair(req: FakeRequest) -> FakeResponse:
    content = _user(req)
    edits = []
    if claim_id(CLAIMS[S_1901][0]) in content:
        edits.append(
            {
                "claim_id": claim_id(CLAIMS[S_1901][0]),
                "op": "correct",
                "replacement": S_FIXED,
                "reason": "the source gives 1887-1889",
            }
        )
    if claim_id(CLAIMS[S_GOLD][0]) in content:
        edits.append({"claim_id": claim_id(CLAIMS[S_GOLD][0]), "op": "remove", "reason": "none"})
    return FakeResponse(text=json.dumps({"edits": edits}))


def claude_handler(req: FakeRequest) -> FakeResponse:
    if req.schema_name == "DecomposeOut":
        return _decompose(req)
    if req.schema_name == "RepairOut":
        return _repair(req)
    if req.schema_name == "GroundingJudgement":  # fallback judge
        return _ground(req)
    if req.kind == "complete":  # generator
        return FakeResponse(text=f"{S_PARIS} {S_1901} {S_HEIGHT}")
    raise AssertionError(f"unexpected claude call {req.schema_name}")


def verifier_handler(req: FakeRequest) -> FakeResponse:
    if req.schema_name == "GroundingJudgement":
        return _ground(req)
    raise AssertionError(f"unexpected verifier call {req.schema_name}")


def make_router(
    claude: Callable[[FakeRequest], FakeResponse] = claude_handler,
    verifier: Callable[[FakeRequest], FakeResponse] = verifier_handler,
) -> tuple[Router, FakeLLM, FakeLLM]:
    c, v = FakeLLM(ANTHROPIC, handler=claude), FakeLLM(VERIFIER, handler=verifier)
    router = Router({ANTHROPIC: c, VERIFIER: v}, default_roles())
    return router, c, v


async def fake_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


def guard(tmp_path: Path, router: Router, preset: str | GuardConfig = "balanced") -> Guard:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(200)))
    return Guard(
        preset,
        router=router,
        trace_dir=tmp_path / "runs",
        http_client=http,
        resolver=fake_resolver,
    )


def assert_replays(report: Report) -> None:
    assert report.trace_path is not None
    assert replay(Path(report.trace_path)) == report


async def test_check_repairs_contradiction_and_removes_unsupported(tmp_path: Path) -> None:
    router, _, verifier = make_router()
    draft = f"{S_PARIS} {S_1901} {S_HEIGHT} {S_GOLD}"
    report = await guard(tmp_path, router).check(draft, sources=SOURCES)

    assert report.original_draft == draft
    assert S_1901 not in report.final_answer and S_GOLD not in report.final_answer
    assert S_FIXED in report.final_answer
    assert report.status == "VERIFIED"
    assert report.stop_cause == "resolved"
    assert len(report.passes) == 2
    assert {r.op for r in report.repairs} == {"correct", "remove"}
    statuses = {r.claim.text: r.status for r in report.claims}
    assert statuses[CLAIMS[S_1901][0]] == "CONTRADICTED"  # removed, kept in the report
    assert statuses[CLAIMS[S_GOLD][0]] == "UNSUPPORTED"
    assert statuses[CLAIMS[S_FIXED][0]] == "SUPPORTED"
    # delta: pass 1 re-verified only the corrected claim
    assert report.passes[1].verified_ids == [claim_id(CLAIMS[S_FIXED][0])]
    assert report.verifier_diversity and report.usage.calls > 0
    assert report.usage.cost_usd >= 0 and verifier.calls
    for r in report.claims:
        if r.claim.id in report.passes[-1].claim_ids:
            assert r.claim.span is not None
            s, e = r.claim.span
            assert report.final_answer[s:e] == r.claim.anchor
    assert report.alerts  # every edit is disclosed
    assert_replays(report)


async def test_fast_preset_blocks_contradiction_without_repair(tmp_path: Path) -> None:
    router, _, _ = make_router()
    draft = f"{S_PARIS} {S_1901}"
    report = await guard(tmp_path, router, "fast").check(draft, sources=SOURCES)
    assert report.status == "BLOCKED"
    assert report.stop_cause == "repair_disabled"
    assert report.repairs == []
    assert report.original_draft == draft and "withheld" in report.final_answer
    assert_replays(report)


async def test_run_generates_then_guards_with_run_id_and_extra_sinks(tmp_path: Path) -> None:
    router, _, _ = make_router()
    seen: list[Event] = []

    async def sink(event: Event) -> None:
        seen.append(event)

    report = await guard(tmp_path, router).run(
        "When was the Eiffel Tower built?",
        sources=SOURCES,
        run_id="fixedrunid01",
        extra_sinks=[sink],
    )
    assert report.run_id == "fixedrunid01"
    assert report.trace_path == str(tmp_path / "runs" / "fixedrunid01.jsonl")
    assert report.original_draft == f"{S_PARIS} {S_1901} {S_HEIGHT}"
    assert S_FIXED in report.final_answer
    assert [e.type for e in seen] == [e.type for e in load_trace(Path(report.trace_path))]
    assert seen[0].data["mode"] == "generate" and seen[-1].type == "run.completed"
    assert_replays(report)


async def test_verifier_outage_falls_back_and_discloses(tmp_path: Path) -> None:
    router, claude, _ = make_router(
        verifier=lambda r: FakeResponse(error="LLMUnavailableError", error_message="503")
    )
    report = await guard(tmp_path, router).check(f"{S_PARIS} {S_HEIGHT}", sources=SOURCES)
    assert not report.verifier_diversity
    assert report.fallbacks
    assert any("fell back" in a.message for a in report.alerts)
    assert any(r.schema_name == "GroundingJudgement" for r in claude.calls)
    assert_replays(report)


async def test_strict_fails_closed_on_verifier_outage(tmp_path: Path) -> None:
    router, _, _ = make_router(
        verifier=lambda r: FakeResponse(error="LLMUnavailableError", error_message="503")
    )
    cfg = get_preset("strict").with_overrides(detectors=("grounding", "deterministic"))
    report = await guard(tmp_path, router, cfg).check(S_PARIS, sources=SOURCES)
    assert cfg.fallback_policy is FallbackPolicy.FAIL_CLOSED
    assert report.verifier_diversity and not report.fallbacks
    assert report.claims[0].status != "SUPPORTED"
    assert report.status == "CAUTION"
    assert_replays(report)


async def test_decompose_failure_completes_as_caution(tmp_path: Path) -> None:
    def claude(req: FakeRequest) -> FakeResponse:
        if req.schema_name == "DecomposeOut":
            return FakeResponse(error="LLMRefusalError")
        return claude_handler(req)

    router, _, _ = make_router(claude=claude)
    report = await guard(tmp_path, router).check(S_PARIS, sources=SOURCES)
    assert report.status == "CAUTION"
    assert report.final_answer == S_PARIS
    assert len(report.alerts) == 1 and "could be verified" in report.alerts[0].message
    assert_replays(report)


async def test_generate_failure_raises_and_trace_records_run_failed(tmp_path: Path) -> None:
    def claude(req: FakeRequest) -> FakeResponse:
        if req.kind == "complete":
            return FakeResponse(error="LLMUnavailableError")
        return claude_handler(req)

    router, _, _ = make_router(claude=claude)
    g = guard(tmp_path, router)
    with pytest.raises(Exception, match="simulated"):
        await g.run("q?", run_id="failedrun001")
    events = load_trace(tmp_path / "runs" / "failedrun001.jsonl")
    assert [e.type for e in events] == ["run.started", "run.failed"]


async def test_wrap_checks_the_wrapped_answer(tmp_path: Path) -> None:
    router, _, _ = make_router()
    g = guard(tmp_path, router, "fast")

    @g.wrap
    async def answer(question: str) -> str:
        return S_PARIS

    report = await answer("Where is it?")
    assert report.question == "Where is it?"
    assert report.original_draft == S_PARIS
