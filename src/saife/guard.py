"""Public SDK entry point.

    guard = Guard("balanced")
    report = await guard.check(answer, sources=[...])   # bring your own answer
    report = await guard.run("question")                # generate and guard

    @guard.wrap
    async def answer(question: str) -> str: ...

Every run writes a JSONL trace to ``trace_dir/<run_id>.jsonl`` (P8).
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import httpx2

from saife.config import GuardConfig, Settings, get_preset, load_settings
from saife.detectors import Resolver, build_detectors
from saife.events import EventBus, Sink
from saife.llm import build_router
from saife.llm.router import Router
from saife.notify.dashboard import dashboard_sink
from saife.pipeline.loop import Pipeline, new_run_id
from saife.trace import TraceSink
from saife.types import Report, Source

SourcesArg = Sequence[Source | str] | None


def as_sources(sources: SourcesArg) -> list[Source]:
    """Accept ``Source`` objects or plain strings (given ids ``s1``, ``s2`` …)."""
    out: list[Source] = []
    for i, s in enumerate(sources or [], start=1):
        out.append(s if isinstance(s, Source) else Source(id=f"s{i}", text=s))
    return out


class Guard:
    def __init__(
        self,
        config: GuardConfig | str = "balanced",
        *,
        router: Router | None = None,
        settings: Settings | None = None,
        sinks: Sequence[Sink] = (),
        trace_dir: Path = Path("runs"),
        http_client: httpx2.AsyncClient | None = None,
        resolver: Resolver | None = None,
        dashboard: bool = True,
    ) -> None:
        """``dashboard``: also send this guard's events to the user's sAIfe Monitor when
        ``SAIFE_DASHBOARD_URL``/``SECRET`` are set (only when settings are loaded here or
        passed in; a guard given a ready-made router and no settings never loads .env)."""
        self.config = get_preset(config) if isinstance(config, str) else config
        if router is None:
            settings = settings or load_settings()
            router = build_router(settings, policy=self.config.fallback_policy)
        self.router = router
        self.sinks = list(sinks)
        if dashboard and settings is not None and (sink := dashboard_sink(settings)):
            self.sinks.append(sink)
        self.trace_dir = Path(trace_dir)
        self.http_client = http_client  # for URL/DOI checks (deterministic detector)
        self.resolver = resolver  # DNS hook for the same; None = real DNS

    async def check(
        self,
        answer: str,
        sources: SourcesArg = None,
        question: str | None = None,
        *,
        run_id: str | None = None,
        extra_sinks: Sequence[Sink] = (),
    ) -> Report:
        """Verify an existing answer. ``question`` enables the consistency detector."""
        return await self._execute(
            answer=answer,
            question=question,
            sources=sources,
            run_id=run_id,
            extra_sinks=extra_sinks,
        )

    async def run(
        self,
        question: str,
        sources: SourcesArg = None,
        *,
        run_id: str | None = None,
        extra_sinks: Sequence[Sink] = (),
    ) -> Report:
        """Generate an answer with the subject model, then verify it."""
        return await self._execute(
            answer=None,
            question=question,
            sources=sources,
            run_id=run_id,
            extra_sinks=extra_sinks,
        )

    def wrap[**P](self, fn: Callable[P, Awaitable[str]]) -> Callable[P, Awaitable[Report]]:
        """Decorate an async answer function so it returns a checked ``Report``.

        A first positional ``str`` argument is taken as the question (for consistency)."""

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> Report:
            answer = await fn(*args, **kwargs)
            question = args[0] if args and isinstance(args[0], str) else None
            return await self.check(answer, question=question)

        return wrapper

    async def _execute(
        self,
        *,
        answer: str | None,
        question: str | None,
        sources: SourcesArg,
        run_id: str | None,
        extra_sinks: Sequence[Sink],
    ) -> Report:
        rid = run_id or new_run_id()
        trace_path = self.trace_dir / f"{rid}.jsonl"
        bus = EventBus(rid)
        bus.add_sink(TraceSink(trace_path))
        for sink in (*self.sinks, *extra_sinks):
            bus.add_sink(sink)
        pipeline = Pipeline(
            self.router,
            self.config,
            detectors=build_detectors(
                self.config, http_client=self.http_client, resolver=self.resolver
            ),
            bus=bus,
        )
        try:
            return await pipeline.run(
                answer=answer,
                question=question,
                sources=as_sources(sources),
                trace_path=str(trace_path),
            )
        finally:
            await bus.drain()
