"""sAIfe -> sAIfe Monitor dashboard: settings, the automatic event sink, bench publishing."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest
from typer.testing import CliRunner

import saife.cli
from saife.config import ConfigError, Settings, load_settings
from saife.guard import Guard
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse
from saife.llm.router import Router
from saife.notify.dashboard import DashboardError, bench_payload, dashboard_sink, publish_bench
from saife.notify.webhook import SIGNATURE_HEADER, WebhookSink, sign
from saife.redact import redact
from tests.conftest import mock_http

URL = "https://my-saife.vercel.app"
SECRET = "dashboard-secret-0123456789"
RESULT = {
    "meta": {"run_name": "2026-10-18-seeded-balanced", "set": "seeded"},
    "metrics": {"precision": 1.0, "recall": 0.9},
    "items": [{"id": "item-1", "draft": "private per-item text"}],
}


def _settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str | None, secret: str | None
) -> Settings:
    for name, value in (("SAIFE_DASHBOARD_URL", url), ("SAIFE_DASHBOARD_SECRET", secret)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return load_settings(env_file=None, probe_path=tmp_path / "x.json")


def test_dashboard_is_optional(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    s = _settings(monkeypatch, tmp_path, None, None)
    assert s.dashboard_url is None and dashboard_sink(s) is None


def test_dashboard_url_is_normalised_and_the_secret_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    s = _settings(monkeypatch, tmp_path, URL + "/", SECRET)
    assert s.dashboard_url == URL and s.dashboard_host == "my-saife.vercel.app"
    assert SECRET not in repr(s)
    assert SECRET not in redact(f"secret={SECRET}")
    sink = dashboard_sink(s)
    assert isinstance(sink, WebhookSink) and "my-saife.vercel.app" in repr(sink)


@pytest.mark.parametrize(
    ("url", "secret", "match"),
    [
        (URL, None, "both"),
        (None, SECRET, "both"),
        ("http://my-saife.vercel.app", SECRET, "https"),
        ("ftp://x", SECRET, "https"),
        (URL, "short", "16 characters"),
    ],
)
def test_bad_dashboard_settings_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str | None, secret: str | None, match: str
) -> None:
    with pytest.raises(ConfigError, match=match):
        _settings(monkeypatch, tmp_path, url, secret)


def test_plain_http_is_allowed_for_a_local_dev_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    s = _settings(monkeypatch, tmp_path, "http://localhost:5174", SECRET)
    assert s.dashboard_url == "http://localhost:5174"


def _router() -> Router:
    from saife.config import ANTHROPIC, VERIFIER, default_roles

    def never(request: FakeRequest) -> FakeResponse:
        raise AssertionError("no model calls expected")

    return Router(
        {ANTHROPIC: FakeLLM(ANTHROPIC, handler=never), VERIFIER: FakeLLM(VERIFIER, handler=never)},
        default_roles(),
    )


def test_guard_adds_the_dashboard_sink_only_when_configured_and_wanted() -> None:
    configured = Settings(dashboard_url=URL, dashboard_secret=SECRET)
    with_sink = Guard("fast", router=_router(), settings=configured)
    assert sum(isinstance(s, WebhookSink) for s in with_sink.sinks) == 1
    opted_out = Guard("fast", router=_router(), settings=configured, dashboard=False)
    assert not any(isinstance(s, WebhookSink) for s in opted_out.sinks)
    # A ready-made router and no settings: .env is never read, so no sink.
    assert not any(isinstance(s, WebhookSink) for s in Guard("fast", router=_router()).sinks)


def test_bench_payload_keeps_meta_and_metrics_only() -> None:
    body = json.loads(bench_payload(RESULT))
    assert set(body) == {"meta", "metrics"}
    assert b"private per-item text" not in bench_payload(RESULT)
    with pytest.raises(DashboardError):
        bench_payload({"meta": {}})


async def test_publish_bench_signs_exactly_what_it_sends() -> None:
    http, seen = mock_http(lambda r: httpx2.Response(200, json={"ok": True, "name": "n1"}))
    settings = Settings(dashboard_url=URL, dashboard_secret=SECRET)
    assert await publish_bench(settings, RESULT, http_client=http) == "n1"
    req = seen[0]
    assert str(req.url) == URL + "/api/bench"
    assert req.headers[SIGNATURE_HEADER] == sign(SECRET, req.content)
    assert SECRET.encode() not in req.content


async def test_publish_bench_reports_refusals_and_missing_config() -> None:
    http, _ = mock_http(lambda r: httpx2.Response(401, json={"error": "bad signature"}))
    with pytest.raises(DashboardError, match="HTTP 401"):
        await publish_bench(
            Settings(dashboard_url=URL, dashboard_secret=SECRET), RESULT, http_client=http
        )
    with pytest.raises(DashboardError, match="not configured"):
        await publish_bench(Settings(), RESULT)


def test_publish_bench_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(RESULT), encoding="utf-8")
    runner = CliRunner()

    # Not configured: clear error, non-zero exit.
    out = runner.invoke(saife.cli.app, ["publish-bench", str(path)])
    assert out.exit_code == 1 and "no dashboard configured" in out.output

    sent: list[dict[str, object]] = []

    async def fake_publish(settings: Settings, result: dict[str, object], **_: object) -> str:
        sent.append(result)
        return "2026-10-18-seeded-balanced"

    monkeypatch.setattr(
        saife.cli,
        "dashboard_settings",
        lambda: Settings(dashboard_url=URL, dashboard_secret=SECRET),
    )
    monkeypatch.setattr("saife.notify.dashboard.publish_bench", fake_publish)
    out = runner.invoke(saife.cli.app, ["publish-bench", str(path)])
    assert out.exit_code == 0, out.output
    assert "published 2026-10-18-seeded-balanced" in out.output
    assert sent and sent[0]["meta"] == RESULT["meta"]

    missing = runner.invoke(saife.cli.app, ["publish-bench", str(tmp_path / "nope.json")])
    assert missing.exit_code == 1 and "cannot read" in missing.output
