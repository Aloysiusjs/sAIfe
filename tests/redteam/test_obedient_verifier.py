"""A verifier that obeys injected instructions still cannot make a claim SUPPORTED (P2, P7).

The obedient judge answers SUPPORTED for everything and quotes the injected text, the
claim itself and fragments of the payload, under real, wrong and made-up source ids.
Code-checked evidence is the only way to SUPPORTED, and none of those quotes is clean
text from the named source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from saife.llm.base import SearchHit
from saife.types import Report, Source
from tests.invariants.harness import PARIS, SOURCE_TEXT, World, config, run_guard

from .payloads import FALSE_CLAIM, INJECTIONS, obedient_judge, obedient_web_judge

PAYLOADS = list(INJECTIONS.items())
IDS = [k for k, _ in PAYLOADS]


def statuses(report: Report) -> dict[str, str]:
    return {r.claim.text: r.status for r in report.claims}


def assert_injection_failed(report: Report, claim: str = FALSE_CLAIM) -> None:
    got = statuses(report)
    assert got[claim] != "SUPPORTED", report.model_dump_json(indent=1)
    assert report.status != "VERIFIED"
    for r in report.claims:
        for v in r.verdicts:
            for e in v.evidence:
                if e.verified:  # whatever did verify is clean source text, never the payload
                    assert e.quote not in INJECTIONS.values()


@pytest.mark.parametrize(("name", "payload"), PAYLOADS, ids=IDS)
async def test_injection_in_sources(tmp_path: Path, name: str, payload: str) -> None:
    sources = [Source(id="s1", text=f"{SOURCE_TEXT}\n\n{payload}")]
    out = await run_guard(
        tmp_path,
        World(judge=obedient_judge),
        answer=f"{PARIS} {FALSE_CLAIM}",
        sources=sources,
        cfg=config("balanced", detectors=("grounding",), repair=False),
    )
    assert out.report is not None, repr(out.error)
    assert_injection_failed(out.report)
    # The clean claim quoted from a clean paragraph still verifies: no collateral damage.
    assert statuses(out.report)[PARIS] == "SUPPORTED"


@pytest.mark.parametrize(("name", "payload"), PAYLOADS, ids=IDS)
async def test_injection_in_draft(tmp_path: Path, name: str, payload: str) -> None:
    """The draft carries the injection; the obedient judge quotes it, but it is not in
    any source, so nothing it says can verify."""
    out = await run_guard(
        tmp_path,
        World(judge=obedient_judge),
        answer=f"{FALSE_CLAIM} {payload}",
        sources=[Source(id="s1", text=SOURCE_TEXT)],
        cfg=config("fast"),
    )
    assert out.report is not None, repr(out.error)
    assert all(r.status != "SUPPORTED" for r in out.report.claims), statuses(out.report)
    assert out.report.status != "VERIFIED"


@pytest.mark.parametrize(("name", "payload"), PAYLOADS, ids=IDS)
async def test_injection_in_search_results(tmp_path: Path, name: str, payload: str) -> None:
    hits = [
        SearchHit(url="https://evil.example/1", title="Tower facts", text=payload),
        SearchHit(url="https://evil.example/2", title="More", text=f"{PARIS}\n\n{payload}"),
    ]
    world = World(search=lambda claim: hits, web_judge=obedient_web_judge)
    out = await run_guard(
        tmp_path,
        world,
        answer=FALSE_CLAIM,
        cfg=config("strict", detectors=("web_evidence",), repair=False),
    )
    assert out.report is not None, repr(out.error)
    assert_injection_failed(out.report)


async def test_known_gap_claim_inside_instruction_block(tmp_path: Path) -> None:
    """The source states the false claim word for word, inside an instruction block.
    Before the hardening in ``saife.evidence`` this quote verified."""
    source = f"{SOURCE_TEXT}\n\n{INJECTIONS['gap']}"
    assert FALSE_CLAIM in source
    out = await run_guard(
        tmp_path,
        World(judge=obedient_judge),
        answer=f"{PARIS} {FALSE_CLAIM}",
        sources=[Source(id="s1", text=source)],
        cfg=config("strict", detectors=("grounding",), repair=False),
    )
    assert out.report is not None
    assert_injection_failed(out.report)
    (false,) = [r for r in out.report.claims if r.claim.text == FALSE_CLAIM]
    quotes = [e for v in false.verdicts for e in v.evidence if e.quote == FALSE_CLAIM]
    assert quotes and not any(e.verified for e in quotes)
    assert statuses(out.report)[PARIS] == "SUPPORTED"


async def test_known_gap_in_web_results(tmp_path: Path) -> None:
    hit = SearchHit(url="https://evil.example/gap", text=INJECTIONS["gap"])
    world = World(search=lambda claim: [hit], web_judge=obedient_web_judge)
    out = await run_guard(
        tmp_path,
        world,
        answer=FALSE_CLAIM,
        cfg=config("strict", detectors=("web_evidence",), repair=False),
    )
    assert out.report is not None
    assert_injection_failed(out.report)
