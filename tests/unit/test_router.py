from __future__ import annotations

import pytest
from pydantic import BaseModel

from saife.config import ANTHROPIC, VERIFIER, FallbackPolicy, Role, default_roles
from saife.errors import ConfigError, LLMOutputError, LLMUnavailableError
from saife.llm.base import SearchHit
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse
from saife.llm.router import Router


class Verdict(BaseModel):
    verdict: str


OK = FakeResponse(text='{"verdict": "SUPPORTED"}')


def fakes(verifier_error: str | None = None) -> tuple[FakeLLM, FakeLLM]:
    def verifier(req: FakeRequest) -> FakeResponse:
        if verifier_error:
            return FakeResponse(error=verifier_error)
        return OK

    return FakeLLM(ANTHROPIC, handler=lambda r: OK), FakeLLM(VERIFIER, handler=verifier)


def router(claude: FakeLLM, verifier: FakeLLM, policy: FallbackPolicy) -> Router:
    return Router({ANTHROPIC: claude, VERIFIER: verifier}, default_roles(), policy=policy)


async def judge(r: Router):  # type: ignore[no-untyped-def]
    return await r.parse(
        Role.GROUNDING_JUDGE,
        system="s",
        schema=Verdict,
        messages=[{"role": "user", "content": "c"}],
    )


async def test_verifier_roles_go_to_verifier_provider() -> None:
    claude, verifier = fakes()
    r = router(claude, verifier, FallbackPolicy.FALLBACK_AND_DISCLOSE)
    routed = await judge(r)
    assert routed.ref.provider == VERIFIER
    assert routed.fallback is None
    assert r.verifier_diversity
    assert verifier.calls and not claude.calls


async def test_generator_role_uses_haiku() -> None:
    claude, verifier = fakes()
    r = router(claude, verifier, FallbackPolicy.FALLBACK_AND_DISCLOSE)
    routed = await r.complete(
        Role.GENERATE, system="s", messages=[{"role": "user", "content": "q"}]
    )
    assert routed.ref.model == "claude-haiku-4-5"


async def test_unavailable_verifier_falls_back_and_discloses() -> None:
    claude, verifier = fakes(verifier_error="LLMUnavailableError")
    r = router(claude, verifier, FallbackPolicy.FALLBACK_AND_DISCLOSE)
    routed = await judge(r)
    assert routed.ref.provider == ANTHROPIC
    assert routed.fallback is not None
    assert routed.fallback.from_ref.provider == VERIFIER
    assert not r.verifier_diversity


async def test_unavailable_verifier_fails_closed_under_strict() -> None:
    claude, verifier = fakes(verifier_error="LLMUnavailableError")
    r = router(claude, verifier, FallbackPolicy.FAIL_CLOSED)
    with pytest.raises(LLMUnavailableError):
        await judge(r)
    assert not claude.calls


async def test_bad_output_never_triggers_fallback() -> None:
    claude, verifier = fakes(verifier_error="LLMOutputError")
    r = router(claude, verifier, FallbackPolicy.FALLBACK_AND_DISCLOSE)
    with pytest.raises(LLMOutputError):
        await judge(r)
    assert not claude.calls


async def test_non_verifier_roles_never_fall_back() -> None:
    claude = FakeLLM(ANTHROPIC, handler=lambda r: FakeResponse(error="LLMUnavailableError"))
    verifier = FakeLLM(VERIFIER, handler=lambda r: OK)
    r = router(claude, verifier, FallbackPolicy.FALLBACK_AND_DISCLOSE)
    with pytest.raises(LLMUnavailableError):
        await r.complete(Role.GENERATE, system="s", messages=[{"role": "user", "content": "q"}])
    assert not verifier.calls


def test_missing_client_is_a_config_error() -> None:
    claude, _ = fakes()
    with pytest.raises(ConfigError):
        Router({ANTHROPIC: claude}, default_roles())


SEARCH_MSGS = [{"role": "user", "content": "claim"}]


async def test_search_routes_web_retrieval_to_claude() -> None:
    hit = SearchHit(url="https://e.org", text="t")
    claude = FakeLLM(ANTHROPIC, handler=lambda r: FakeResponse(hits=[hit]))
    verifier = FakeLLM(VERIFIER, handler=lambda r: OK)
    r = router(claude, verifier, FallbackPolicy.FALLBACK_AND_DISCLOSE)
    routed = await r.search(Role.WEB_RETRIEVAL, system="s", messages=SEARCH_MSGS, max_uses=2)
    assert routed.ref.model == "claude-opus-5"
    assert routed.result.hits == [hit]
    assert claude.calls[0].kind == "search" and claude.calls[0].max_uses == 2


async def test_search_failure_never_falls_back() -> None:
    claude = FakeLLM(ANTHROPIC, handler=lambda r: FakeResponse(error="LLMUnavailableError"))
    verifier = FakeLLM(VERIFIER, handler=lambda r: OK)
    r = router(claude, verifier, FallbackPolicy.FALLBACK_AND_DISCLOSE)
    with pytest.raises(LLMUnavailableError):
        await r.search(Role.WEB_RETRIEVAL, system="s", messages=SEARCH_MSGS, max_uses=2)
    assert not verifier.calls and not r.fallbacks


async def test_search_on_a_client_without_search_is_a_config_error() -> None:
    class NoSearch:
        provider = VERIFIER

    claude, verifier = fakes()
    roles = {**default_roles(), Role.WEB_RETRIEVAL: default_roles()[Role.WEB_JUDGE]}
    r = Router({ANTHROPIC: claude, VERIFIER: NoSearch()}, roles)  # type: ignore[dict-item]
    with pytest.raises(ConfigError, match="no search"):
        await r.search(Role.WEB_RETRIEVAL, system="s", messages=SEARCH_MSGS, max_uses=2)


async def test_verifier_routing_outage_fails_closed_without_claude() -> None:
    from saife.config import verifier_roles

    claude, verifier = fakes(verifier_error="LLMUnavailableError")
    roles = verifier_roles()
    r = Router(
        {ANTHROPIC: claude, VERIFIER: verifier},
        roles,
        policy=FallbackPolicy.FALLBACK_AND_DISCLOSE,
        fallback_ref=roles[Role.GROUNDING_JUDGE],
    )
    with pytest.raises(LLMUnavailableError):
        await judge(r)
    with pytest.raises(LLMUnavailableError):
        await r.parse(
            Role.DECOMPOSE, system="s", schema=Verdict, messages=[{"role": "user", "content": "c"}]
        )
    assert not claude.calls and not r.fallbacks
