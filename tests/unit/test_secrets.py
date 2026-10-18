"""PRD N4: API keys never reach logs, reprs or error messages."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx2
import pytest

from saife.config import load_settings
from saife.errors import LLMUnavailableError
from saife.llm.openai_compat import OpenAICompatClient
from saife.redact import REDACTED, RedactingFilter, redact, register_secrets
from tests.conftest import FAKE_ANTHROPIC_KEY, FAKE_VERIFIER_KEY, mock_http


def test_patterns_redact_key_shapes() -> None:
    text = f"anthropic={FAKE_ANTHROPIC_KEY} auth=Bearer abcdefghijklmnop1234"
    out = redact(text)
    assert FAKE_ANTHROPIC_KEY not in out
    assert "abcdefghijklmnop1234" not in out
    assert out.count(REDACTED) == 2


def test_registered_secret_with_unusual_shape_is_redacted() -> None:
    odd = "gw-key.ZZZ-not-a-known-pattern-999"
    register_secrets([odd])
    assert odd not in redact(f"key is {odd}")


def test_logging_filter_redacts_args(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("saife.test")
    caplog.handler.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="saife.test"):
        logger.info("calling with %s", FAKE_VERIFIER_KEY)
    assert FAKE_VERIFIER_KEY not in caplog.text
    assert REDACTED in caplog.text


def test_settings_repr_hides_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAIFE_VERIFIER_API_KEY", FAKE_VERIFIER_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)
    monkeypatch.setenv("SAIFE_VERIFIER_BASE_URL", "https://gw.example/v1")
    s = load_settings(env_file=None, probe_path=tmp_path / "none.json")
    for text in (repr(s), str(s)):
        assert FAKE_VERIFIER_KEY not in text
        assert FAKE_ANTHROPIC_KEY not in text
        assert "gw.example" in text


async def test_error_messages_do_not_contain_key() -> None:
    http, _ = mock_http(lambda r: httpx2.Response(401, json={"error": {"message": "bad key"}}))
    c = OpenAICompatClient(
        api_key=FAKE_VERIFIER_KEY, base_url="https://gw.example/v1", max_retries=0, http_client=http
    )
    with pytest.raises(LLMUnavailableError) as info:
        await c.complete(model="m", system="s", messages=[{"role": "user", "content": "x"}])
    assert FAKE_VERIFIER_KEY not in str(info.value)
