from __future__ import annotations

import importlib
import socket
import sys
from collections.abc import Callable

import httpx2
import pytest

from saife.config import ANTHROPIC, VERIFIER, default_roles, get_preset
from saife.detectors import deterministic as det
from saife.detectors.base import DetectorContext
from saife.detectors.deterministic import NO_CHECK, DeterministicDetector
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse
from saife.llm.router import Router
from saife.types import Claim, ClaimKind, DetectorVerdict
from tests.conftest import mock_http

PUBLIC_IP = "93.184.215.14"


def _never(req: FakeRequest) -> FakeResponse:
    raise AssertionError("the deterministic detector must not call a model")


def make_ctx() -> DetectorContext:
    router = Router(
        {
            ANTHROPIC: FakeLLM(ANTHROPIC, handler=_never),
            VERIFIER: FakeLLM(VERIFIER, handler=_never),
        },
        default_roles(),
    )
    return DetectorContext(draft="", config=get_preset("fast"), router=router)


def claim(text: str, kind: ClaimKind = "numeric", anchor: str | None = None) -> Claim:
    return Claim.make(text, anchor=text if anchor is None else anchor, span=None, kind=kind)


async def public_dns(host: str) -> list[str]:
    return [PUBLIC_IP]


async def run(
    text: str,
    kind: ClaimKind = "numeric",
    responder: Callable[[httpx2.Request], httpx2.Response] | None = None,
    resolver: det.Resolver = public_dns,
    wait: float = 5.0,
    anchor: str | None = None,
) -> tuple[DetectorVerdict, list[httpx2.Request]]:
    http, seen = mock_http(responder or (lambda r: httpx2.Response(200)))
    detector = DeterministicDetector(http, timeout=wait, resolver=resolver)
    [verdict] = await detector.check([claim(text, kind, anchor)], make_ctx())
    return verdict, seen


def assert_well_formed(v: DetectorVerdict) -> None:
    assert v.detector == "deterministic"
    assert v.support_kind == "computed"
    for e in v.evidence:
        assert e.method == "computed" and e.verified and e.source_id.startswith("deterministic:")
    if v.verdict in ("SUPPORTED", "CONTRADICTED"):
        assert v.has_verified_evidence
    elif v.verdict == "NOT_FOUND":
        # A partial check may attach what it computed, as information, never as support.
        assert v.confidence == 0.0
        assert not v.evidence or PARTIAL_ONLY in (v.rationale or "")
    else:
        assert v.confidence == 0.0 and not v.evidence


PARTIAL_ONLY = "the rest of the claim is not checked"


# --- (a) arithmetic ---------------------------------------------------------

ARITHMETIC = [
    ("12 × 4 = 48", "SUPPORTED", "12 × 4 = 48"),
    ("12 x 4 is 48", "SUPPORTED", None),
    ("12 * 4 = 49", "CONTRADICTED", "12 × 4 = 48, claim says 49"),
    ("100 / 3 = 33.33", "SUPPORTED", None),
    ("100 / 3 = 33.4", "CONTRADICTED", None),
    ("1,200 + 300 = 1,500", "SUPPORTED", None),
    ("5 - 8 = -3", "SUPPORTED", None),
    ("5 - 8 = 3", "CONTRADICTED", None),
    ("7 / 0 = 0", "CONTRADICTED", "division by zero"),
    ("2 + 3 × 4 = 14", "NOT_FOUND", None),  # chained expression: skipped, not misread
    ("15% of 200 is 30", "SUPPORTED", "15% of 200 = 30"),
    ("15 percent of $200 equals $35", "CONTRADICTED", "claim says 35"),
    # A consistent percent change is partial: the figures are not checked.
    ("Sales increased from 40 to 50, a 25% rise", "NOT_FOUND", "+25.0%"),
    ("from 40 to 50 is a 25% rise", "SUPPORTED", "+25.0%"),
    (
        "Sales increased from 40 to 50, a 30% rise",
        "CONTRADICTED",
        "40→50 is +25.0%, claim says 30%",
    ),
    ("It fell from 50 to 40, a 20% drop", "SUPPORTED", None),
    ("It went from 40 to 50, a 25% decrease", "CONTRADICTED", "claim says a 25% fall"),
    ("Users grew from 30 to 40, a 33% increase", "NOT_FOUND", PARTIAL_ONLY),
    ("from 0 to 5, a 10% rise", "NOT_FOUND", None),
    # word operators and verbs
    ("12 multiplied by 12 equals 144", "SUPPORTED", "12 × 12 = 144"),
    ("12 times 12 is 144", "SUPPORTED", None),
    ("12 times 12 makes 145", "CONTRADICTED", "claim says 145"),
    ("144 divided by 12 gives 12", "SUPPORTED", "144 ÷ 12 = 12"),
    ("7 plus 5 is equal to 12", "SUPPORTED", None),
    ("10 minus 4 equals 6", "SUPPORTED", None),
    ("12 × 12 = 144.", "SUPPORTED", None),
    ("12 ÷ 4 = 3", "SUPPORTED", None),
    ("2 plus 3 times 4 equals 14", "NOT_FOUND", None),  # chained, word form
    ("12 × 12 = 144 × 2", "NOT_FOUND", None),  # chained on the right
    # no false positives
    ("Python 3.0 is newer than Python 2", "NOT_FOUND", None),
    ("version 2 is 3x faster", "NOT_FOUND", None),
    ("2 x 3 is 7x faster in version 2", "NOT_FOUND", None),
    ("Python 3 times out after 2 seconds", "NOT_FOUND", None),
]


@pytest.mark.parametrize(("text", "verdict", "detail"), ARITHMETIC)
async def test_arithmetic(text: str, verdict: str, detail: str | None) -> None:
    v, seen = await run(text)
    assert v.verdict == verdict, v.rationale
    assert_well_formed(v)
    assert not seen
    if detail:
        assert detail in (v.rationale or "")


async def test_pure_arithmetic_is_full_support_but_percent_change_is_partial() -> None:
    arithmetic, _ = await run("12 × 4 = 48")
    change, _ = await run("from 40 to 50 is a 25% rise")
    with_figures, _ = await run("Revenue increased from 40 to 50, a 25% rise")
    assert arithmetic.verdict == "SUPPORTED" and arithmetic.confidence == 1.0
    assert change.verdict == "SUPPORTED"
    assert change.confidence < 1.0 and "not checked" in (change.rationale or "")
    # Revenue's figures are unchecked: the computation is information, not support.
    assert with_figures.verdict == "NOT_FOUND" and with_figures.confidence == 0.0
    assert [e.quote for e in with_figures.evidence] == ["40→50 is +25.0%"]
    assert PARTIAL_ONLY in (with_figures.rationale or "")


async def test_reworded_arithmetic_is_checked_on_the_anchor() -> None:
    # Live run: the draft said "12 * 12 = 144"; the decomposer reworded it.
    v, _ = await run("Twelve squared, 12 by 12, comes out at 144", anchor="12 * 12 = 144")
    assert v.verdict == "SUPPORTED" and v.confidence == 1.0
    assert [e.quote for e in v.evidence] == ["12 × 12 = 144"]


async def test_anchor_error_is_merged_worst_wins() -> None:
    v, _ = await run("12 multiplied by 12 equals 144", anchor="12 * 12 = 145")
    assert v.verdict == "CONTRADICTED"


async def test_anchor_arithmetic_for_another_claim_is_ignored() -> None:
    # Claims from one sentence share an anchor; another claim's sum is not this one's.
    v, _ = await run("Paris is in France", kind="entity", anchor="2 + 2 = 5 and Paris is in France")
    assert v.verdict == "NOT_FOUND" and v.rationale == NO_CHECK


async def test_same_finding_on_text_and_anchor_is_reported_once() -> None:
    v, _ = await run("12 multiplied by 12 equals 144", anchor="12 * 12 = 144")
    assert v.verdict == "SUPPORTED"
    assert [e.quote for e in v.evidence] == ["12 × 12 = 144"]


# --- (b) dates --------------------------------------------------------------

DATES = [
    ("1 January 2000 was a Saturday", "SUPPORTED", "1 January 2000 was a Saturday"),
    ("January 1, 2000 was a Sunday", "CONTRADICTED", "claim says Sunday"),
    ("Saturday, 1st January 2000, was quiet", "SUPPORTED", None),
    ("2024-07-04 fell on a Thursday", "SUPPORTED", None),
    ("The meeting was on February 30", "CONTRADICTED", "February has 29 days"),
    ("31 April 2021 is a date", "CONTRADICTED", "April 2021 has 30 days"),
    ("February 29, 2023 was busy", "CONTRADICTED", "February 2023 has 28 days"),
    ("February 29, 2024 was busy", "NOT_FOUND", None),
    ("2023-13-01 was the release", "CONTRADICTED", "no month 13"),
    ("X (1990) was before Y (1985)", "CONTRADICTED", "1990 is after 1985, claim says before"),
    ("X (1980) was before Y (1985)", "NOT_FOUND", "1980 is before 1985"),
    ("X (1990) came after Y (1985)", "NOT_FOUND", PARTIAL_ONLY),
    ("X (1990) was not before Y (1985)", "NOT_FOUND", None),
    ("X (2000) came 10 years after Y (1990)", "NOT_FOUND", PARTIAL_ONLY),
    ("X (2000) came 15 years after Y (1990)", "CONTRADICTED", "claim says 15"),
    ("X (1990) was 10 years before Y (2000)", "NOT_FOUND", PARTIAL_ONLY),
    ("There were 12 years between 1990 and 2000", "CONTRADICTED", "1990 to 2000 is 10 years"),
    ("between 1990 and 2000 is a span of 10 years", "SUPPORTED", None),
    ("X (2000) came about 11 years after Y (1990)", "NOT_FOUND", PARTIAL_ONLY),
    ("X (2000) came more than 12 years after Y (1990)", "CONTRADICTED", "more than 12"),
]


@pytest.mark.parametrize(("text", "verdict", "detail"), DATES)
async def test_dates(text: str, verdict: str, detail: str | None) -> None:
    v, _ = await run(text, kind="date")
    assert v.verdict == verdict, v.rationale
    assert_well_formed(v)
    if detail:
        assert detail in (v.rationale or "")


async def test_year_checks_are_partial() -> None:
    v, _ = await run("X (1980) was before Y (1985)", kind="date")
    assert v.verdict == "NOT_FOUND" and v.confidence == 0.0
    assert [e.quote for e in v.evidence] == ["1980 is before 1985"]
    assert v.rationale == f"1980 is before 1985; {PARTIAL_ONLY}"
    pure, _ = await run("between 1990 and 2000 is a span of 10 years", kind="date")
    assert pure.verdict == "SUPPORTED"
    assert pure.confidence < 1.0 and "years are not checked" in (pure.rationale or "")


# --- (c) URLs and DOIs ------------------------------------------------------


def status(code: int) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda r: httpx2.Response(code)


def raises(exc: type[Exception]) -> Callable[[httpx2.Request], httpx2.Response]:
    def responder(r: httpx2.Request) -> httpx2.Response:
        raise exc("boom", request=r)  # type: ignore[call-arg]

    return responder


URL = "See https://example.com/paper.pdf."
REFERENCES = [
    # (text, kind, responder, verdict, confidence)
    (URL, "citation", status(200), "SUPPORTED", 0.5),
    (URL, "citation", status(301), "SUPPORTED", 0.5),
    (URL, "citation", status(404), "CONTRADICTED", 0.8),
    (URL, "citation", status(410), "CONTRADICTED", 0.8),
    (URL, "entity", status(404), "NOT_FOUND", 0.0),
    (URL, "citation", status(500), "UNVERIFIABLE", 0.0),
    (URL, "citation", status(503), "UNVERIFIABLE", 0.0),
    (URL, "citation", status(429), "UNVERIFIABLE", 0.0),
    (URL, "citation", status(403), "UNVERIFIABLE", 0.0),
    (URL, "citation", raises(httpx2.ConnectTimeout), "UNVERIFIABLE", 0.0),
    (URL, "citation", raises(httpx2.ReadTimeout), "UNVERIFIABLE", 0.0),
    (URL, "citation", raises(httpx2.ConnectError), "CONTRADICTED", 0.8),
    (URL, "entity", raises(httpx2.ConnectError), "NOT_FOUND", 0.0),
    (URL, "citation", raises(httpx2.RemoteProtocolError), "UNVERIFIABLE", 0.0),
]


@pytest.mark.parametrize(("text", "kind", "responder", "verdict", "confidence"), REFERENCES)
async def test_reference_status(
    text: str,
    kind: ClaimKind,
    responder: Callable[[httpx2.Request], httpx2.Response],
    verdict: str,
    confidence: float,
) -> None:
    v, seen = await run(text, kind=kind, responder=responder)
    assert v.verdict == verdict, v.rationale
    assert v.confidence == confidence
    assert_well_formed(v)
    assert [str(r.url) for r in seen] == ["https://example.com/paper.pdf"]
    assert seen[0].method == "HEAD"
    if verdict == "SUPPORTED":
        assert "content is not checked" in (v.rationale or "")
    if verdict == "UNVERIFIABLE":
        assert v.error


async def test_head_405_falls_back_to_get() -> None:
    v, seen = await run(
        URL,
        kind="citation",
        responder=lambda r: httpx2.Response(405 if r.method == "HEAD" else 200),
    )
    assert [r.method for r in seen] == ["HEAD", "GET"]
    assert v.verdict == "SUPPORTED"
    assert "GET https://example.com/paper.pdf → HTTP 200" in v.evidence[0].quote


async def test_doi_resolves_via_doi_org() -> None:
    v, seen = await run(
        "Smith et al., doi:10.1234/abc.5678.", kind="citation", responder=status(302)
    )
    assert [str(r.url) for r in seen] == ["https://doi.org/10.1234/abc.5678"]
    # The DOI exists, but that Smith et al. wrote it is not checked.
    assert v.verdict == "NOT_FOUND" and PARTIAL_ONLY in (v.rationale or "")
    assert v.evidence[0].source_id == "deterministic:doi"
    pure, _ = await run("doi:10.1234/abc.5678", kind="citation", responder=status(302))
    assert pure.verdict == "SUPPORTED"


async def test_citation_anchor_link_is_checked_but_not_support() -> None:
    v, seen = await run(
        "The benchmark paper reports a 2x speedup",
        kind="citation",
        anchor="the paper (https://example.com/paper.pdf) reports a 2x speedup",
    )
    assert [str(r.url) for r in seen] == ["https://example.com/paper.pdf"]
    assert v.verdict == "NOT_FOUND" and v.evidence and PARTIAL_ONLY in (v.rationale or "")


async def test_other_claims_anchor_links_are_not_fetched() -> None:
    _, seen = await run("Paris is in France", kind="entity", anchor="Paris (https://x.example.org)")
    assert not seen


async def test_missing_doi_contradicts_citation() -> None:
    v, _ = await run("See 10.9999/does-not-exist", kind="citation", responder=status(404))
    assert v.verdict == "CONTRADICTED"
    assert "DOI 10.9999/does-not-exist" in (v.rationale or "")


async def test_doi_inside_url_is_checked_once() -> None:
    _, seen = await run("https://doi.org/10.1234/xyz", kind="citation")
    assert [str(r.url) for r in seen] == ["https://doi.org/10.1234/xyz"]


async def test_any_missing_reference_contradicts_even_if_others_exist() -> None:
    v, seen = await run(
        "https://a.example.org/x and https://b.example.org/y",
        kind="citation",
        responder=lambda r: httpx2.Response(404 if r.url.host == "b.example.org" else 200),
    )
    assert len(seen) == 2
    assert v.verdict == "CONTRADICTED"
    assert [e.quote for e in v.evidence] == [
        "cited https://b.example.org/y does not resolve: HEAD https://b.example.org/y → HTTP 404"
    ]


async def test_outage_on_one_reference_blocks_support() -> None:
    v, _ = await run(
        "https://a.example.org/x and https://b.example.org/y",
        kind="citation",
        responder=lambda r: httpx2.Response(503 if r.url.host == "b.example.org" else 200),
    )
    assert v.verdict == "UNVERIFIABLE"


SSRF = [
    "http://localhost:8080/admin",
    "http://127.0.0.1/",
    "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/x",
    "http://8.8.8.8/",  # any IP literal
    "http://2130706433/",  # decimal 127.0.0.1
    "http://0x7f.1/",
    "http://intranet/wiki",
    "http://printer.local/",
    "http://metadata.google.internal/",
    "http://user:pass@example.com/",
]


@pytest.mark.parametrize("url", SSRF)
async def test_ssrf_guard_blocks_without_request(url: str) -> None:
    v, seen = await run(f"Source: {url}", kind="citation")
    assert not seen
    assert v.verdict == "NOT_FOUND"
    assert "not fetched" in (v.rationale or "")


@pytest.mark.parametrize(
    "addr", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "::ffff:10.0.0.1"]
)
async def test_ssrf_guard_blocks_hosts_resolving_to_private_addresses(addr: str) -> None:
    async def rebinding(host: str) -> list[str]:
        return [PUBLIC_IP, addr]

    v, seen = await run(URL, kind="citation", resolver=rebinding)
    assert not seen
    assert v.verdict == "NOT_FOUND"
    assert "non-public address" in (v.rationale or "")


@pytest.mark.parametrize("kind", ["citation", "entity"])
async def test_dns_failure(kind: ClaimKind) -> None:
    async def nxdomain(host: str) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    v, seen = await run(URL, kind=kind, resolver=nxdomain)
    assert not seen
    assert v.verdict == ("CONTRADICTED" if kind == "citation" else "NOT_FOUND")
    assert "DNS lookup" in (v.rationale or "")


async def test_only_http_schemes_are_considered() -> None:
    v, seen = await run("Download from ftp://example.com/file or file:///etc/passwd", "citation")
    assert not seen
    assert v.verdict == "NOT_FOUND" and v.rationale == NO_CHECK


async def test_hung_resolver_times_out_as_unverifiable() -> None:
    import asyncio

    async def hang(host: str) -> list[str]:
        await asyncio.sleep(10)
        return [PUBLIC_IP]

    v, _ = await run(URL, kind="citation", resolver=hang, wait=0.01)
    assert v.verdict == "UNVERIFIABLE"
    assert v.error and "timed out" in v.error


async def test_references_are_capped_per_claim() -> None:
    urls = " ".join(f"https://example.com/{i}" for i in range(det.MAX_REFS_PER_CLAIM + 2))
    v, seen = await run(urls, kind="citation")
    assert len(seen) == det.MAX_REFS_PER_CLAIM
    assert v.verdict == "NOT_FOUND"  # unchecked references keep it from reading as support
    assert "2 more reference(s) not checked" in (v.rationale or "")


async def test_requests_run_concurrently_and_are_deduplicated() -> None:
    import asyncio

    active = 0
    peak = 0

    async def slow_dns(host: str) -> list[str]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return [PUBLIC_IP]

    http, seen = mock_http(lambda r: httpx2.Response(200))
    detector = DeterministicDetector(http, resolver=slow_dns)
    claims = [
        claim(f"https://example.com/{i} https://example.com/shared", "citation") for i in range(3)
    ]
    verdicts = await detector.check(claims, make_ctx())
    assert peak > 1
    assert sorted(str(r.url) for r in seen) == sorted(
        [f"https://example.com/{i}" for i in range(3)] + ["https://example.com/shared"]
    )
    assert [v.claim_id for v in verdicts] == [c.id for c in claims]
    assert all(v.verdict == "SUPPORTED" for v in verdicts)


async def test_default_client_is_created_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[httpx2.AsyncClient] = []
    real = httpx2.AsyncClient

    def factory(**kwargs: object) -> httpx2.AsyncClient:
        client = real(transport=httpx2.MockTransport(lambda r: httpx2.Response(200)))
        created.append(client)
        return client

    monkeypatch.setattr(httpx2, "AsyncClient", factory)
    detector = DeterministicDetector(resolver=public_dns)
    [v] = await detector.check([claim(URL, "citation")], make_ctx())
    assert v.verdict == "SUPPORTED"
    assert len(created) == 1 and created[0].is_closed


async def test_no_client_created_without_references(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(**kwargs: object) -> httpx2.AsyncClient:
        raise AssertionError("no HTTP client needed")

    monkeypatch.setattr(httpx2, "AsyncClient", factory)
    [v] = await DeterministicDetector().check([claim("12 × 4 = 48")], make_ctx())
    assert v.verdict == "SUPPORTED"


# --- (d) code_api -----------------------------------------------------------

CODE_API = [
    # Existence is checked, behaviour is not: support only when existence is the claim.
    ("json.loads exists", "SUPPORTED"),
    ("json.loads is a function in the json module", "SUPPORTED"),
    ("os.path.join is available in the standard library", "SUPPORTED"),
    ("json.loads parses a JSON string", "NOT_FOUND"),
    ("Use os.path.join to build paths", "NOT_FOUND"),
    ("Use os.path.joinx to build paths", "CONTRADICTED"),
    ("datetime.datetime.now returns the current time", "NOT_FOUND"),
    ("datetime.datetime.nowish returns the current time", "CONTRADICTED"),
    ("urllib.parse.urlsplit splits URLs", "NOT_FOUND"),
    ("math.tau is 2π", "NOT_FOUND"),
    ("An unnamed function is about twice as fast as json.loads.", "NOT_FOUND"),  # live run
    ("json.loads and json.parse both exist", "CONTRADICTED"),
    ("requests.get fetches a URL", "NOT_FOUND"),  # not allowlisted: never imported
    ("subprocess.run runs a command", "NOT_FOUND"),
    ("json.parse does not exist", "NOT_FOUND"),  # negated: not checked
]


@pytest.mark.parametrize(("text", "verdict"), CODE_API)
async def test_code_api(text: str, verdict: str) -> None:
    v, _ = await run(text, kind="code_api")
    assert v.verdict == verdict, v.rationale
    assert_well_formed(v)
    if verdict == "SUPPORTED":
        assert v.confidence < 1.0 and "behaviour is not checked" in (v.rationale or "")
    if verdict == "NOT_FOUND" and v.evidence:
        assert v.rationale and v.rationale.endswith(f"exists ({PY}); {PARTIAL_ONLY}")


PY = f"Python {sys.version_info.major}.{sys.version_info.minor}"


@pytest.mark.parametrize(
    ("text", "pure"),
    [
        ("json.loads exists", True),
        ("json.loads is a function in the json module", True),
        ("15% of 200 is 30", True),
        ("12 × 12 = 144", True),
        ("See https://example.com/a.", True),
        ("An unnamed function is about twice as fast as json.loads.", False),
        ("json.loads is not a function", False),
        ("Smith et al., doi:10.1234/abc", False),
    ],
)
def test_purity_heuristic(text: str, pure: bool) -> None:
    assert det._is_pure(text) is pure


async def test_code_api_only_for_code_api_claims() -> None:
    v, _ = await run("os.path.joinx builds paths", kind="entity")
    assert v.verdict == "NOT_FOUND" and v.rationale == NO_CHECK


async def test_code_api_never_imports_outside_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    imported: list[str] = []
    real = importlib.import_module

    def spy(name: str, package: str | None = None) -> object:
        imported.append(name)
        return real(name, package)

    monkeypatch.setattr(importlib, "import_module", spy)
    await run("antigravity.fly and os.system and json.loads and pickle.loads", kind="code_api")
    assert imported and set(imported) <= det.CODE_API_ALLOWLIST


# --- general ----------------------------------------------------------------


async def test_no_check_applies_is_not_support() -> None:
    v, seen = await run("Paris is the capital of France", kind="entity")
    assert not seen
    assert v.verdict == "NOT_FOUND" and v.confidence == 0.0 and v.rationale == NO_CHECK
    assert_well_formed(v)


async def test_applies_always() -> None:
    assert DeterministicDetector().applies(make_ctx())


async def test_one_verdict_per_claim_in_order() -> None:
    claims = [claim("12 × 4 = 48"), claim("Paris is in France", "entity"), claim("2 + 2 = 5")]
    verdicts = await DeterministicDetector(resolver=public_dns).check(claims, make_ctx())
    assert [v.claim_id for v in verdicts] == [c.id for c in claims]
    assert [v.verdict for v in verdicts] == ["SUPPORTED", "NOT_FOUND", "CONTRADICTED"]


async def test_contradiction_wins_over_support_in_one_claim() -> None:
    v, _ = await run("12 × 4 = 48 and 2 + 2 = 5")
    assert v.verdict == "CONTRADICTED"
    assert [e.quote for e in v.evidence] == ["2 + 2 = 4, claim says 5"]


async def test_crashing_check_is_unverifiable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(text: str) -> list[object]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(det, "_arithmetic", boom)
    v, _ = await run("12 × 4 = 48")
    assert v.verdict == "UNVERIFIABLE"
    assert v.error and "arithmetic check failed" in v.error and "kaboom" in v.error


async def test_crashing_resolver_is_unverifiable() -> None:
    async def broken(host: str) -> list[str]:
        raise RuntimeError("resolver bug")

    v, _ = await run(URL, kind="citation", resolver=broken)
    assert v.verdict == "UNVERIFIABLE"
    assert v.error and "resolver bug" in v.error


async def test_detector_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = DeterministicDetector(resolver=public_dns)
    real_combine = det._combine
    calls = 0

    def flaky(claim: Claim, findings: list[det._Finding]) -> DetectorVerdict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("combine bug")
        return real_combine(claim, findings)

    monkeypatch.setattr(det, "_combine", flaky)
    [v] = await detector.check([claim("12 × 4 = 48")], make_ctx())
    assert v.verdict == "UNVERIFIABLE"
    assert v.error and "combine bug" in v.error


async def test_code_api_does_not_walk_into_non_allowlisted_modules() -> None:
    # os.sys is the sys module, which is not allowlisted: no verdict either way.
    v, _ = await run("os.sys.nothing_here exists", kind="code_api")
    assert v.verdict == "NOT_FOUND"
