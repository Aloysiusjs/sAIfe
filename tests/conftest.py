from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

FAKE_VERIFIER_KEY = "sk-test-verifier-0123456789abcdef"
FAKE_ANTHROPIC_KEY = "sk-ant-test-0123456789abcdef"

Responder = Callable[[httpx2.Request], httpx2.Response]


def mock_http(responder: Responder) -> tuple[httpx2.AsyncClient, list[httpx2.Request]]:
    """An httpx2 client whose transport records requests and answers with ``responder``."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return responder(request)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), seen


def openai_chat(content: str, finish_reason: str = "stop", model: str = "m") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def anthropic_message(
    text: str, stop_reason: str = "end_turn", model: str = "claude-haiku-4-5"
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }


def body(request: httpx2.Request) -> dict[str, Any]:
    return json.loads(request.content)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never see real keys from the developer's environment."""
    for var in (
        "ANTHROPIC_API_KEY",
        "SAIFE_VERIFIER_API_KEY",
        "SAIFE_VERIFIER_BASE_URL",
        "SAIFE_VERIFIER_MODEL",
        "SAIFE_VERIFIER_STRUCTURED_MODE",
        "SAIFE_ROUTING",
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "SAIFE_DASHBOARD_URL",
        "SAIFE_DASHBOARD_SECRET",
        *(
            f"SAIFE_ROLE_{r}"
            for r in (
                "GENERATE",
                "DECOMPOSE",
                "WEB_RETRIEVAL",
                "GROUNDING_JUDGE",
                "CONSISTENCY_JUDGE",
                "WEB_JUDGE",
                "REPAIR",
            )
        ),
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _no_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI benchmark runs never publish to a real dashboard from the developer's .env."""
    import saife.cli

    monkeypatch.setattr(saife.cli, "dashboard_settings", lambda: None)
