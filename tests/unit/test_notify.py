from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging

import httpx2
import pytest
from rich.console import Console

from saife.events import EventBus
from saife.notify import SIGNATURE_HEADER, CallbackSink, ConsoleSink, WebhookSink
from saife.types import Claim, ClaimResult, Event
from tests.conftest import mock_http

SECRET = "whsec-test-shared-secret"
URL = "https://hooks.example.com/saife/endpoint?token=tok-should-not-be-logged"


def _event(seq: int = 0) -> Event:
    return Event(type="pass.started", run_id="r1", seq=seq, ts=1.0, data={"index": 0})


def _expected_sig(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


async def test_webhook_signs_exact_body() -> None:
    client, seen = mock_http(lambda r: httpx2.Response(200))
    sink = WebhookSink(URL, SECRET, http_client=client)
    await sink(_event())
    assert len(seen) == 1
    req = seen[0]
    assert req.method == "POST"
    assert req.headers[SIGNATURE_HEADER] == _expected_sig(req.content)
    assert json.loads(req.content)["type"] == "pass.started"
    assert SECRET not in req.content.decode()


async def test_webhook_retries_then_succeeds() -> None:
    statuses = iter([503, 429, 200])
    client, seen = mock_http(lambda r: httpx2.Response(next(statuses)))
    delays: list[float] = []

    async def fake_sleep(s: float) -> None:
        delays.append(s)

    sink = WebhookSink(URL, SECRET, http_client=client, backoff=0.5, sleep=fake_sleep)
    await sink(_event())
    assert len(seen) == 3
    assert delays == [0.5, 1.0]
    # Every attempt carries the same, valid signature.
    assert {r.headers[SIGNATURE_HEADER] for r in seen} == {_expected_sig(seen[0].content)}


async def test_webhook_retries_connect_errors() -> None:
    calls = {"n": 0}

    def responder(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx2.ConnectError("refused", request=request)
        return httpx2.Response(204)

    client, _ = mock_http(responder)
    await WebhookSink(URL, SECRET, http_client=client, backoff=0)(_event())
    assert calls["n"] == 2


async def test_webhook_final_failure_logs_redacted_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, seen = mock_http(lambda r: httpx2.Response(500))
    sink = WebhookSink(URL, SECRET, http_client=client, retries=3, backoff=0)
    with caplog.at_level(logging.WARNING, logger="saife.notify.webhook"):
        await sink(_event())
    assert len(seen) == 4
    assert "hooks.example.com" in caplog.text
    assert "HTTP 500" in caplog.text
    assert SECRET not in caplog.text
    assert "tok-should-not-be-logged" not in caplog.text
    assert SIGNATURE_HEADER not in caplog.text


async def test_webhook_does_not_retry_client_errors() -> None:
    client, seen = mock_http(lambda r: httpx2.Response(400))
    await WebhookSink(URL, SECRET, http_client=client, backoff=0)(_event())
    assert len(seen) == 1


async def test_dead_webhook_does_not_fail_run() -> None:
    def responder(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    client, seen = mock_http(responder)
    bus = EventBus("r1")
    bus.add_sink(WebhookSink(URL, SECRET, http_client=client, retries=2, backoff=0))
    await bus.emit("pass.started", index=0, draft="d")
    await bus.emit("run.completed", status="VERIFIED")
    await bus.drain()
    assert len(seen) == 6
    assert [json.loads(r.content)["seq"] for r in seen] == [0, 0, 0, 1, 1, 1]


async def test_callback_sink_sync_and_async() -> None:
    got: list[str] = []

    def sync_fn(e: Event) -> None:
        got.append(f"sync{e.seq}")

    async def async_fn(e: Event) -> None:
        got.append(f"async{e.seq}")

    await CallbackSink(sync_fn)(_event(0))
    await CallbackSink(async_fn)(_event(1))
    assert got == ["sync0", "async1"]


async def test_console_lines_have_icon_and_label() -> None:
    buf = io.StringIO()
    console = Console(file=buf, width=200, color_system=None)
    bus = EventBus("r1")
    bus.add_sink(ConsoleSink(console))
    claim = Claim.make("The moon is [bold]cheese[/bold].", anchor="moon", span=None, kind="entity")
    await bus.emit("run.started", question="q?", preset="balanced", mode="generate", sources=[])
    await bus.emit(
        "claim.verdict",
        pass_index=0,
        result=ClaimResult(claim=claim, status="CONTRADICTED", risk=0.9),
    )
    await bus.emit(
        "claim.verdict",
        pass_index=0,
        result=ClaimResult(claim=claim, status="UNVERIFIABLE", risk=0.5),
    )
    await bus.emit("run.completed", status="BLOCKED")
    lines = buf.getvalue().splitlines()
    assert len(lines) == 4
    assert "✗ CONTRADICTED" in lines[1]
    assert "[bold]cheese[/bold]" in lines[1]  # untrusted text not parsed as markup
    assert "! UNVERIFIABLE" in lines[2]
    assert "✗ BLOCKED" in lines[3]
