from __future__ import annotations

import asyncio
import logging

import pytest

from saife.errors import SaifeError
from saife.events import EventBus
from saife.types import Claim, Event


async def _collect(bus: EventBus) -> list[Event]:
    return [e async for e in bus.stream()]


async def test_seq_is_monotonic_and_sinks_see_order() -> None:
    bus = EventBus("r1")
    seen_a: list[int] = []
    seen_b: list[int] = []
    calls: list[str] = []

    async def a(e: Event) -> None:
        seen_a.append(e.seq)
        calls.append(f"a{e.seq}")

    async def b(e: Event) -> None:
        seen_b.append(e.seq)
        calls.append(f"b{e.seq}")

    bus.add_sink(a)
    bus.add_sink(b)
    await bus.emit("run.started", question="q", preset="balanced", mode="generate", sources=[])
    await bus.emit("pass.started", index=0, draft="d")
    await bus.emit("run.failed", error="boom")
    assert [e.seq for e in bus.history] == [0, 1, 2]
    assert all(e.run_id == "r1" for e in bus.history)
    assert seen_a == seen_b == [0, 1, 2]
    assert calls == ["a0", "b0", "a1", "b1", "a2", "b2"]
    ts = [e.ts for e in bus.history]
    assert ts == sorted(ts)


async def test_emit_model_dumps_pydantic_values() -> None:
    bus = EventBus("r1")
    claim = Claim.make("Paris is in France.", anchor="Paris", span=(0, 5), kind="entity")
    event = await bus.emit("claim.extracted", pass_index=0, claim=claim, extra=[claim])
    assert event.data["claim"] == claim.model_dump(mode="json")
    assert event.data["claim"]["span"] == [0, 5]
    assert event.data["extra"] == [claim.model_dump(mode="json")]
    Event.model_validate_json(event.model_dump_json())


async def test_late_subscriber_gets_history_then_live_without_gaps() -> None:
    bus = EventBus("r1")
    early = asyncio.create_task(_collect(bus))
    await asyncio.sleep(0)
    for i in range(3):
        await bus.emit("pass.started", index=i, draft="d")
    late = asyncio.create_task(_collect(bus))
    await asyncio.sleep(0)
    for i in range(3, 5):
        await bus.emit("pass.started", index=i, draft="d")
    await bus.emit("run.completed", status="VERIFIED")
    after = await _collect(bus)  # subscribes after the run finished
    expected = list(range(6))
    for events in (await early, await late, after):
        assert [e.seq for e in events] == expected


async def test_close_ends_streams_and_blocks_emit() -> None:
    bus = EventBus("r1")
    sub = asyncio.create_task(_collect(bus))
    await bus.emit("pass.started", index=0, draft="d")
    await bus.close()
    assert [e.seq for e in await asyncio.wait_for(sub, 1)] == [0]
    with pytest.raises(SaifeError):
        await bus.emit("pass.started", index=1, draft="d")


async def test_terminal_event_closes_bus() -> None:
    bus = EventBus("r1")
    await bus.emit("run.completed", status="VERIFIED")
    assert bus.closed
    with pytest.raises(SaifeError):
        await bus.emit("pass.started", index=0, draft="d")


async def test_failing_sink_is_logged_and_does_not_break_bus(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = EventBus("r1")
    good: list[int] = []

    async def bad(e: Event) -> None:
        raise RuntimeError("sink exploded sk-ant-test-0123456789abcdef")

    async def ok(e: Event) -> None:
        good.append(e.seq)

    bus.add_sink(bad)
    bus.add_sink(ok)
    with caplog.at_level(logging.WARNING, logger="saife.events"):
        await bus.emit("pass.started", index=0, draft="d")
        await bus.emit("run.failed", error="x")
    assert good == [0, 1]
    assert "sink exploded" in caplog.text
    assert "sk-ant-test-0123456789abcdef" not in caplog.text


class _SlowBackground:
    background = True

    def __init__(self) -> None:
        self.seen: list[int] = []
        self.release = asyncio.Event()

    async def __call__(self, event: Event) -> None:
        await self.release.wait()
        self.seen.append(event.seq)


async def test_background_sink_does_not_block_and_drain_waits() -> None:
    bus = EventBus("r1")
    sink = _SlowBackground()
    bus.add_sink(sink)
    for i in range(3):
        await asyncio.wait_for(bus.emit("pass.started", index=i, draft="d"), 1)
    assert sink.seen == []  # emit returned without waiting for delivery
    sink.release.set()
    await asyncio.wait_for(bus.drain(), 1)
    assert sink.seen == [0, 1, 2]
