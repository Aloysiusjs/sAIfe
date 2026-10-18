from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel

from saife.errors import LLMError, LLMRefusalError, LLMTruncatedError, LLMUnavailableError
from saife.llm.anthropic import (
    FALLBACK_BETA,
    MAX_PAUSE_CONTINUATIONS,
    AnthropicClient,
    accepts_sampling,
)
from tests.conftest import FAKE_ANTHROPIC_KEY, anthropic_message, body, mock_http


class Claims(BaseModel):
    claims: list[str]


def client(responder):  # type: ignore[no-untyped-def]
    http, seen = mock_http(responder)
    return AnthropicClient(api_key=FAKE_ANTHROPIC_KEY, max_retries=0, http_client=http), seen


async def test_haiku_complete_sends_temperature_without_fallbacks() -> None:
    c, seen = client(lambda r: httpx2.Response(200, json=anthropic_message("Paris.")))
    res = await c.complete(
        model="claude-haiku-4-5",
        system="s",
        temperature=1.0,
        messages=[{"role": "user", "content": "Capital of France?"}],
    )
    assert res.text == "Paris."
    sent = body(seen[0])
    assert sent["temperature"] == 1.0
    assert "fallbacks" not in sent
    assert "anthropic-beta" not in seen[0].headers


async def test_opus_parse_enables_default_fallbacks() -> None:
    reply = anthropic_message('{"claims": ["a", "b"]}', model="claude-opus-5")
    c, seen = client(lambda r: httpx2.Response(200, json=reply))
    res = await c.parse(
        model="claude-opus-5",
        system="s",
        schema=Claims,
        messages=[{"role": "user", "content": "split"}],
    )
    assert res.parsed.claims == ["a", "b"]
    assert res.usage.output_tokens == 7
    sent = body(seen[0])
    assert sent["fallbacks"] == "default"
    assert FALLBACK_BETA in seen[0].headers.get("anthropic-beta", "")


async def test_temperature_on_opus_is_rejected_before_any_request() -> None:
    c, seen = client(lambda r: httpx2.Response(200, json=anthropic_message("x")))
    with pytest.raises(ValueError, match="temperature"):
        await c.complete(
            model="claude-opus-5",
            system="s",
            temperature=0.5,
            messages=[{"role": "user", "content": "x"}],
        )
    assert seen == []


@pytest.mark.parametrize(
    ("stop", "exc"), [("refusal", LLMRefusalError), ("max_tokens", LLMTruncatedError)]
)
async def test_incomplete_stop_reasons_raise(stop: str, exc: type[LLMError]) -> None:
    c, _ = client(lambda r: httpx2.Response(200, json=anthropic_message("x", stop_reason=stop)))
    with pytest.raises(exc):
        await c.complete(
            model="claude-haiku-4-5", system="s", messages=[{"role": "user", "content": "x"}]
        )


async def test_server_error_is_unavailable_and_400_is_not() -> None:
    err = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    c, _ = client(lambda r: httpx2.Response(500, json=err))
    with pytest.raises(LLMUnavailableError):
        await c.complete(
            model="claude-haiku-4-5", system="s", messages=[{"role": "user", "content": "x"}]
        )

    bad = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    c, _ = client(lambda r: httpx2.Response(400, json=bad))
    with pytest.raises(LLMError) as info:
        await c.complete(
            model="claude-haiku-4-5", system="s", messages=[{"role": "user", "content": "x"}]
        )
    assert not isinstance(info.value, LLMUnavailableError)


def test_accepts_sampling() -> None:
    assert accepts_sampling("claude-haiku-4-5")
    assert not accepts_sampling("claude-opus-5")


# --- web search -------------------------------------------------------------------

URL = "https://example.org/france"
CITED = "Paris is the capital and largest city of France."


def search_message(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> dict[str, Any]:
    msg = anthropic_message("", stop_reason=stop_reason, model="claude-opus-5")
    msg["content"] = content
    return msg


def tool_use(i: int) -> dict[str, Any]:
    return {
        "type": "server_tool_use",
        "id": f"srvtoolu_{i}",
        "name": "web_search",
        "input": {"query": "capital of France"},
    }


def tool_result(i: int, content: Any) -> dict[str, Any]:
    return {"type": "web_search_tool_result", "tool_use_id": f"srvtoolu_{i}", "content": content}


RESULTS = [
    {
        "type": "web_search_result",
        "url": URL,
        "title": "France",
        "encrypted_content": "opaque",
        "page_age": "3 days ago",
    },
    {
        "type": "web_search_result",
        "url": "https://other.example",
        "title": "Other",
        "encrypted_content": "opaque",
    },
]

CITED_TEXT = {
    "type": "text",
    "text": "Paris is the capital.",
    "citations": [
        {
            "type": "web_search_result_location",
            "url": URL,
            "title": "France",
            "encrypted_index": "idx",
            "cited_text": CITED,
        }
    ],
}

USER = [{"role": "user", "content": "claim"}]


def scripted(*replies: dict[str, Any]):  # type: ignore[no-untyped-def]
    queue = list(replies)
    return client(lambda r: httpx2.Response(200, json=queue.pop(0)))


async def test_search_sends_tool_and_collects_cited_text() -> None:
    c, seen = scripted(search_message([tool_use(1), tool_result(1, RESULTS), CITED_TEXT]))
    res = await c.search(model="claude-opus-5", system="s", messages=USER, max_uses=3)
    sent = body(seen[0])
    # The basic tool: the dynamic-filtering variant leaves no citations (see fixture tests).
    assert sent["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    assert sent["output_config"] == {"effort": "low"}
    by_url = {h.url: h for h in res.hits}
    assert by_url[URL].text == CITED
    assert by_url[URL].page_age == "3 days ago"
    # A result nobody cited has no readable text; encrypted_content is never used.
    assert by_url["https://other.example"].text == ""
    assert res.continuations == 0


async def test_search_resumes_pause_turn_without_extra_user_message() -> None:
    c, seen = scripted(
        search_message([tool_use(1), tool_result(1, RESULTS)], stop_reason="pause_turn"),
        search_message([CITED_TEXT]),
    )
    res = await c.search(model="claude-opus-5", system="s", messages=USER, max_uses=3)
    assert len(seen) == 2
    resumed = body(seen[1])["messages"]
    assert [m["role"] for m in resumed] == ["user", "assistant"]
    assert [b["type"] for b in resumed[1]["content"]] == [
        "server_tool_use",
        "web_search_tool_result",
    ]
    assert res.continuations == 1
    assert res.usage.output_tokens == 14  # summed over both calls
    assert {h.url: h.text for h in res.hits}[URL] == CITED


async def test_search_gives_up_after_too_many_pauses() -> None:
    paused = search_message([tool_use(1), tool_result(1, RESULTS)], stop_reason="pause_turn")
    c, seen = client(lambda r: httpx2.Response(200, json=paused))
    with pytest.raises(LLMTruncatedError, match="paused"):
        await c.search(model="claude-opus-5", system="s", messages=USER, max_uses=3)
    assert len(seen) == MAX_PAUSE_CONTINUATIONS + 1


async def test_search_error_object_raises_unavailable() -> None:
    err = {"type": "web_search_tool_result_error", "error_code": "unavailable"}
    c, _ = scripted(search_message([tool_use(1), tool_result(1, err), CITED_TEXT]))
    with pytest.raises(LLMUnavailableError, match="unavailable"):
        await c.search(model="claude-opus-5", system="s", messages=USER, max_uses=3)


async def test_max_uses_exceeded_after_good_results_is_not_fatal() -> None:
    err = {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}
    c, _ = scripted(
        search_message(
            [tool_use(1), tool_result(1, RESULTS), tool_use(2), tool_result(2, err), CITED_TEXT]
        )
    )
    res = await c.search(model="claude-opus-5", system="s", messages=USER, max_uses=1)
    assert any(h.text == CITED for h in res.hits)


# --- recorded live responses (tests/fixtures, sanitized: no keys, headers or ciphertext) ---

FIXTURES = Path(__file__).parents[1] / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def prose(msg: dict[str, Any]) -> list[str]:
    return [b["text"] for b in msg["content"] if b["type"] == "text"]


async def test_recorded_basic_search_yields_cited_passages_only() -> None:
    """Real response for four claims: readable text comes only from ``cited_text``."""
    msg = fixture("anthropic_web_search_basic.json")
    c, _ = scripted(msg)
    res = await c.search(model="claude-opus-5", system="s", messages=USER, max_uses=6)
    by_url = {h.url: h for h in res.hits}
    assert len(by_url) == len(res.hits) > 10  # every search result is listed once
    docs = by_url["https://docs.python.org/3/whatsnew/3.0.html"]
    assert "Python 3.0 was released on December 3, 2008." in docs.text
    readable = [h for h in res.hits if h.text]
    assert 5 <= len(readable) < len(res.hits)  # uncited results stay unreadable
    for h in readable:
        assert not h.text.endswith("...")  # the API's truncation marker is stripped
        assert "redacted" not in h.text  # encrypted fields never become text
    # The model's own prose (headings, commentary) never leaks into hit text.
    commentary = "No source found mentioning any `json.parse_fast`"
    assert any(commentary in t for t in prose(msg))
    assert not any(commentary in h.text for h in res.hits)
    assert res.usage.input_tokens == msg["usage"]["input_tokens"]


async def test_recorded_dynamic_filtering_search_has_no_readable_text() -> None:
    """Why the basic tool: with web_search_20260209 the pages are read inside code
    execution and the answer carries no citations, so every hit is unreadable even
    though the prose quotes the right passage. This was the live 'no readable
    results' bug."""
    msg = fixture("anthropic_web_search_dynamic.json")
    c, _ = scripted(msg)
    res = await c.search(model="claude-opus-5", system="s", messages=USER, max_uses=3)
    assert res.hits
    assert all(h.text == "" for h in res.hits)
    assert any("December 3rd, 2008" in t for t in prose(msg))
