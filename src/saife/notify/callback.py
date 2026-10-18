"""Adapts a plain Python function (sync or async) into a sink for SDK users."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from saife.types import Event

EventCallback = Callable[[Event], Awaitable[None] | None]


class CallbackSink:
    def __init__(self, fn: EventCallback) -> None:
        self._fn = fn

    async def __call__(self, event: Event) -> None:
        result = self._fn(event)
        if inspect.isawaitable(result):
            await result
