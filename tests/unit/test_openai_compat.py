from __future__ import annotations

import json
from typing import Literal

import httpx2
import pytest
from pydantic import BaseModel

from saife.errors import (
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    LLMTruncatedError,
    LLMUnavailableError,
)
from saife.llm.openai_compat import OpenAICompatClient, extract_json
from tests.conftest import FAKE_VERIFIER_KEY, body, mock_http, openai_chat

MODEL = "gemini-3.1-flash-lite"
GOOD = '{"verdict": "SUPPORTED", "confidence": 0.9}'


class Verdict(BaseModel):
    verdict: Literal["SUPPORTED", "CONTRADICTED", "NOT_FOUND"]
    confidence: float


def client(responder, mode="json_schema"):  # type: ignore[no-untyped-def]
    http, seen = mock_http(responder)
    c = OpenAICompatClient(
        api_key=FAKE_VERIFIER_KEY,
        base_url="https://gw.example/v1",
        structured_mode=mode,
        max_retries=0,
        http_client=http,
    )
    return c, seen


async def parse(c: OpenAICompatClient):  # type: ignore[no-untyped-def]
    return await c.parse(
        model=MODEL, system="judge", schema=Verdict, messages=[{"role": "user", "content": "claim"}]
    )


async def test_json_schema_mode_sends_schema_and_validates() -> None:
    c, seen = client(lambda r: httpx2.Response(200, json=openai_chat(GOOD, model=MODEL)))
    res = await parse(c)
    assert res.parsed.verdict == "SUPPORTED"
    assert res.usage.input_tokens == 10
    sent = body(seen[0])
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["name"] == "Verdict"
    assert sent["messages"][0] == {"role": "system", "content": "judge"}


async def test_gateway_rejecting_json_schema_downgrades_to_json_object() -> None:
    def responder(r: httpx2.Request) -> httpx2.Response:
        if body(r).get("response_format", {}).get("type") == "json_schema":
            return httpx2.Response(400, json={"error": {"message": "response_format unsupported"}})
        return httpx2.Response(200, json=openai_chat(GOOD))

    c, seen = client(responder)
    res = await parse(c)
    assert res.parsed.verdict == "SUPPORTED"
    assert c.structured_mode == "json_object"
    second = body(seen[1])
    assert second["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in second["messages"][0]["content"]


async def test_text_mode_extracts_fenced_json() -> None:
    reply = f"Here you go:\n```json\n{GOOD}\n```"
    c, seen = client(lambda r: httpx2.Response(200, json=openai_chat(reply)), mode="text")
    res = await parse(c)
    assert res.parsed.confidence == 0.9
    assert "response_format" not in body(seen[0])


async def test_invalid_output_gets_one_repair_retry() -> None:
    replies = iter(['{"verdict": "MAYBE", "confidence": 1}', GOOD])
    c, seen = client(lambda r: httpx2.Response(200, json=openai_chat(next(replies))))
    res = await parse(c)
    assert res.parsed.verdict == "SUPPORTED"
    assert len(seen) == 2
    assert "did not validate" in body(seen[1])["messages"][-1]["content"]
    assert res.usage.input_tokens == 20  # usage summed across both attempts


async def test_invalid_output_twice_fails_closed() -> None:
    c, seen = client(lambda r: httpx2.Response(200, json=openai_chat("not json at all")))
    with pytest.raises(LLMOutputError):
        await parse(c)
    assert len(seen) == 2


@pytest.mark.parametrize(
    ("finish", "exc"),
    [("content_filter", LLMRefusalError), ("length", LLMTruncatedError)],
)
async def test_incomplete_finish_reasons_raise(finish: str, exc: type[LLMError]) -> None:
    c, _ = client(lambda r: httpx2.Response(200, json=openai_chat(GOOD, finish_reason=finish)))
    with pytest.raises(exc):
        await parse(c)


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_auth_rate_limit_and_server_errors_are_unavailable(status: int) -> None:
    c, _ = client(lambda r: httpx2.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(LLMUnavailableError):
        await parse(c)


async def test_connection_error_is_unavailable() -> None:
    def boom(r: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=r)

    c, _ = client(boom)
    with pytest.raises(LLMUnavailableError):
        await parse(c)


async def test_complete_returns_text_and_sends_temperature() -> None:
    c, seen = client(lambda r: httpx2.Response(200, json=openai_chat("hello")))
    res = await c.complete(
        model=MODEL, system="s", temperature=1.0, messages=[{"role": "user", "content": "hi"}]
    )
    assert res.text == "hello"
    assert body(seen[0])["temperature"] == 1.0


def test_extract_json_variants() -> None:
    assert json.loads(extract_json('noise {"a": 1} noise')) == {"a": 1}
    assert json.loads(extract_json('```json\n{"a": 2}\n```')) == {"a": 2}


class Items(BaseModel):
    items: list[Verdict]


async def _parse_items(reply: str) -> Items:
    c, seen = client(lambda r: httpx2.Response(200, json=openai_chat(reply)))
    res = await c.parse(
        model=MODEL, system="s", schema=Items, messages=[{"role": "user", "content": "x"}]
    )
    assert len(seen) == 1  # accepted without a repair retry
    return res.parsed


async def test_bare_array_is_wrapped_for_a_single_list_schema() -> None:
    """Gemini sometimes answers {"items": [...]} with a fenced bare array."""
    reply = f"```json\n[{GOOD}, {GOOD}]\n```"
    assert len((await _parse_items(reply)).items) == 2
    assert len((await _parse_items(f"[{GOOD}]")).items) == 1


async def test_bare_array_items_are_still_validated() -> None:
    bad = '[{"verdict": "MAYBE", "confidence": 1}]'
    c, _ = client(lambda r: httpx2.Response(200, json=openai_chat(bad)))
    with pytest.raises(LLMOutputError):
        await c.parse(
            model=MODEL, system="s", schema=Items, messages=[{"role": "user", "content": "x"}]
        )


def _or_client(responder):  # type: ignore[no-untyped-def]
    http, seen = mock_http(responder)
    c = OpenAICompatClient(
        api_key=FAKE_VERIFIER_KEY,
        base_url="https://or.example/v1",
        max_retries=0,
        http_client=http,
        provider="openrouter",
        extra_body={"reasoning": {"enabled": False}},
        structured_extra_body={"provider": {"require_parameters": True}},
    )
    return c, seen


async def test_extra_body_on_every_call_structured_extras_only_with_a_schema() -> None:
    c, seen = _or_client(lambda r: httpx2.Response(200, json=openai_chat(GOOD)))
    await c.complete(model=MODEL, system="s", messages=[{"role": "user", "content": "q"}])
    await parse(c)
    plain, structured = body(seen[0]), body(seen[1])
    assert plain["reasoning"] == {"enabled": False} and "provider" not in plain
    assert structured["reasoning"] == {"enabled": False}
    assert structured["provider"] == {"require_parameters": True}


async def test_errors_and_results_carry_the_provider_name() -> None:
    c, _ = _or_client(lambda r: httpx2.Response(200, json=openai_chat(GOOD)))
    res = await parse(c)
    assert res.provider == "openrouter"
    c, _ = _or_client(lambda r: httpx2.Response(503, json={"error": {"message": "down"}}))
    with pytest.raises(LLMUnavailableError) as info:
        await parse(c)
    assert info.value.provider == "openrouter"


async def test_last_attempts_counts_the_repair_retry() -> None:
    replies = iter(['{"verdict": "MAYBE", "confidence": 1}', GOOD])
    c, _ = client(lambda r: httpx2.Response(200, json=openai_chat(next(replies))))
    await parse(c)
    assert c.last_attempts == 2
    c, _ = client(lambda r: httpx2.Response(200, json=openai_chat(GOOD)))
    await parse(c)
    assert c.last_attempts == 1
