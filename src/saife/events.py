"""Async event bus: every run transition is emitted once and fanned out to sinks.

Sinks run in registration order and are awaited inline, so the console and the
trace see events in ``seq`` order. A sink that sets ``background = True`` (the
webhook) is scheduled as a task the bus tracks instead, so slow network delivery
never blocks the run; ``drain()`` waits for those tasks. A failing sink is logged
and never breaks the run.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable
from functools import partial
from typing import Any, Protocol

from pydantic import BaseModel

from saife.errors import SaifeError
from saife.redact import redact
from saife.types import Event, EventType

log = logging.getLogger(__name__)

TERMINAL: frozenset[EventType] = frozenset({"run.completed", "run.failed"})


class Sink(Protocol):
    async def __call__(self, event: Event) -> None: ...


def to_jsonable(value: Any) -> Any:
    """Model-dump pydantic values (also inside lists/dicts) so ``Event.data`` is plain JSON."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    return value


def _sink_name(sink: Sink) -> str:
    return type(sink).__name__


class EventBus:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.history: list[Event] = []
        self._sinks: list[Sink] = []
        self._cond = asyncio.Condition()
        self._closed = False
        self._tasks: set[asyncio.Task[None]] = set()
        # Last delivery task per background sink, so its deliveries stay in seq order.
        self._tails: dict[int, asyncio.Task[None]] = {}

    @property
    def closed(self) -> bool:
        return self._closed

    def add_sink(self, sink: Sink) -> None:
        self._sinks.append(sink)

    async def emit(self, type: EventType, **data: Any) -> Event:
        if self._closed:
            raise SaifeError(f"event bus for run {self.run_id} is closed; cannot emit {type}")
        event = Event(
            type=type,
            run_id=self.run_id,
            seq=len(self.history),
            ts=time.time(),
            data=to_jsonable(data),
        )
        async with self._cond:
            self.history.append(event)
            if type in TERMINAL:
                self._closed = True
            self._cond.notify_all()
        for sink in self._sinks:
            if getattr(sink, "background", False):
                self._schedule(sink, event)
            else:
                await self._deliver(sink, event)
        return event

    async def close(self) -> None:
        """End all streams for a run aborted without a terminal event. Idempotent."""
        async with self._cond:
            self._closed = True
            self._cond.notify_all()

    async def drain(self) -> None:
        """Wait for every pending background delivery (call before the process exits)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def stream(self) -> AsyncIterator[Event]:
        """History first, then live events; ends after the terminal event or ``close()``.

        Each subscriber keeps its own cursor into ``history``, so late subscribers get
        every event exactly once, with no gap between replay and live.
        """
        cursor = 0
        while True:
            async with self._cond:
                await self._cond.wait_for(partial(self._ready, cursor))
                batch = self.history[cursor:]
                done = self._closed
            for event in batch:
                yield event
            cursor += len(batch)
            if done and cursor >= len(self.history):
                return

    def _ready(self, cursor: int) -> bool:
        return cursor < len(self.history) or self._closed

    def _schedule(self, sink: Sink, event: Event) -> None:
        prev = self._tails.get(id(sink))
        task = asyncio.create_task(self._deliver_after(prev, sink, event))
        self._tails[id(sink)] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver_after(self, prev: Awaitable[None] | None, sink: Sink, event: Event) -> None:
        if prev is not None:
            await prev  # _deliver never raises
        await self._deliver(sink, event)

    async def _deliver(self, sink: Sink, event: Event) -> None:
        try:
            await sink(event)
        except Exception as exc:  # a sink must never break the run
            log.warning(
                "sink %s failed on %s #%d: %s: %s",
                _sink_name(sink),
                event.type,
                event.seq,
                type(exc).__name__,
                redact(str(exc)),
            )
