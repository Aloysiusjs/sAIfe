"""Send runs and benchmark results to a deployed sAIfe Monitor dashboard (``dashboard/``).

The dashboard is the user's own Vercel deployment. sAIfe reaches it with two settings
from ``.env``: ``SAIFE_DASHBOARD_URL`` (e.g. ``https://my-saife.vercel.app``) and
``SAIFE_DASHBOARD_SECRET`` (the same value set on Vercel). Every pipeline event goes to
``/api/ingest`` through the HMAC-signed ``WebhookSink``; benchmark summaries go to
``/api/bench`` with the same signature. Delivery problems never fail a run.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2

from saife.config import Settings
from saife.errors import SaifeError
from saife.notify.webhook import SIGNATURE_HEADER, WebhookSink, sign
from saife.redact import redact

INGEST_PATH = "/api/ingest"
BENCH_PATH = "/api/bench"
_TIMEOUT_S = 15.0


class DashboardError(SaifeError):
    """The dashboard is not configured, or it refused a benchmark upload."""


def dashboard_sink(settings: Settings) -> WebhookSink | None:
    """A webhook sink for the configured dashboard, or None when none is configured."""
    if not settings.dashboard_url or not settings.dashboard_secret:
        return None
    return WebhookSink(settings.dashboard_url + INGEST_PATH, settings.dashboard_secret)


def bench_payload(result: dict[str, Any]) -> bytes:
    """Only ``meta`` and ``metrics`` leave the machine: per-item details stay local."""
    if not isinstance(result.get("meta"), dict) or not isinstance(result.get("metrics"), dict):
        raise DashboardError("not a sAIfe benchmark result (needs 'meta' and 'metrics')")
    body = json.dumps({"meta": result["meta"], "metrics": result["metrics"]}, ensure_ascii=False)
    return redact(body).encode("utf-8")


async def publish_bench(
    settings: Settings,
    result: dict[str, Any],
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> str:
    """POST a benchmark summary to the dashboard. Returns the stored result name."""
    if not settings.dashboard_url or not settings.dashboard_secret:
        raise DashboardError(
            "dashboard not configured: set SAIFE_DASHBOARD_URL and SAIFE_DASHBOARD_SECRET"
        )
    body = bench_payload(result)
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign(settings.dashboard_secret, body),
    }
    url = settings.dashboard_url + BENCH_PATH
    try:
        if http_client is not None:
            response = await http_client.post(url, content=body, headers=headers)
        else:
            async with httpx2.AsyncClient(timeout=_TIMEOUT_S) as client:
                response = await client.post(url, content=body, headers=headers)
    except httpx2.TransportError as exc:
        raise DashboardError(f"could not reach the dashboard: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise DashboardError(f"dashboard refused the upload: HTTP {response.status_code}")
    try:
        return str(response.json().get("name", ""))
    except ValueError:
        return ""
