"""Event sinks for people and systems outside the run: console, Python callback, webhook."""

from __future__ import annotations

from saife.notify.callback import CallbackSink
from saife.notify.console import ConsoleSink
from saife.notify.webhook import SIGNATURE_HEADER, WebhookSink, sign

__all__ = ["SIGNATURE_HEADER", "CallbackSink", "ConsoleSink", "WebhookSink", "sign"]
