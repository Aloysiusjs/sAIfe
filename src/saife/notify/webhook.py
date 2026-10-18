"""HMAC-signed webhook delivery.

The body is the event JSON (redacted), and ``X-Saife-Signature: sha256=<hex>`` is
the HMAC-SHA256 of exactly those bytes, so receivers verify what they got.
Delivery retries on 5xx/429/transport errors with exponential backoff, then logs
and gives up: a dead webhook must never fail or block a run. The sink sets
``background = True`` so the ``EventBus`` runs it as a tracked task.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx2

from saife.redact import redact
from saife.types import Event

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Saife-Signature"
_TIMEOUT_S = 10.0


def sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


class WebhookSink:
    background = True

    def __init__(
        self,
        url: str,
        secret: str,
        *,
        http_client: httpx2.AsyncClient | None = None,
        retries: int = 3,
        backoff: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._url = url
        self._secret = secret
        self._client = http_client
        self._retries = max(0, retries)
        self._backoff = backoff
        self._sleep = sleep
        # Only the host is ever logged: paths and queries can carry tokens.
        self._host = urlsplit(url).hostname or "<webhook>"

    def __repr__(self) -> str:
        return f"WebhookSink(host={self._host!r})"

    async def __call__(self, event: Event) -> None:
        body = redact(event.model_dump_json()).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign(self._secret, body),
            "X-Saife-Event": event.type,
        }
        if self._client is not None:
            await self._send(self._client, event, body, headers)
        else:
            async with httpx2.AsyncClient(timeout=_TIMEOUT_S) as client:
                await self._send(client, event, body, headers)

    async def _send(
        self, client: httpx2.AsyncClient, event: Event, body: bytes, headers: dict[str, str]
    ) -> None:
        attempts = self._retries + 1
        reason = "no attempt made"
        for attempt in range(attempts):
            if attempt:
                await self._sleep(self._backoff * 2 ** (attempt - 1))
            try:
                response = await client.post(self._url, content=body, headers=headers)
            except httpx2.TransportError as exc:
                reason = type(exc).__name__
                continue
            if response.status_code < 400:
                return
            reason = f"HTTP {response.status_code}"
            if not _retryable(response.status_code):
                break
        log.warning(
            "webhook delivery to %s failed for %s #%d after %d attempt(s): %s",
            self._host,
            event.type,
            event.seq,
            attempt + 1,
            redact(reason),
        )
