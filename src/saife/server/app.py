"""FastAPI server: start runs, stream their events over SSE, fetch reports (M3).

``uv run uvicorn saife.server.app:app`` serves the API on :8000 and, if
``web/dist`` exists, the built demo UI at ``/``.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sse_starlette import EventSourceResponse, ServerSentEvent

from saife.config import PRESETS
from saife.guard import Guard
from saife.types import Event, Report, Source

log = logging.getLogger(__name__)

TERMINAL = frozenset({"run.completed", "run.failed"})
MAX_BODY_BYTES = 1_000_000
MAX_ACTIVE_RUNS = 8
MAX_KEPT_RUNS = 200
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
REPO_ROOT = Path(__file__).resolve().parents[3]
TRAPS_PATH = REPO_ROOT / "bench" / "datasets" / "traps" / "traps.jsonl"
WEB_DIST = REPO_ROOT / "web" / "dist"
RESULTS_DIR = REPO_ROOT / "bench" / "results"
MAX_RESULT_BYTES = 5_000_000

GuardFactory = Callable[[str], Guard]


class SourceIn(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100_000)
    title: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2000)


class RunRequest(BaseModel):
    question: str | None = Field(default=None, max_length=4000)
    answer: str | None = Field(default=None, max_length=40_000)
    sources: list[SourceIn] = Field(default_factory=list, max_length=20)
    preset: Literal["fast", "balanced", "strict"] = "balanced"

    @model_validator(mode="after")
    def _needs_input(self) -> RunRequest:
        if not (self.question or "").strip() and not (self.answer or "").strip():
            raise ValueError("provide a question, an answer, or both")
        return self


@dataclass
class RunState:
    run_id: str
    events: list[Event] = field(default_factory=list)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    report: Report | None = None
    task: asyncio.Task[None] | None = None

    @property
    def done(self) -> bool:
        return bool(self.events) and self.events[-1].type in TERMINAL

    async def add(self, event: Event) -> None:
        async with self.changed:
            self.events.append(event)
            self.changed.notify_all()

    def _has_more_than(self, n: int) -> bool:
        return len(self.events) > n

    async def stream(self) -> AsyncIterator[Event]:
        """All past events, then live ones; ends after the terminal event."""
        i = 0
        while True:
            async with self.changed:
                await self.changed.wait_for(functools.partial(self._has_more_than, i))
                batch = self.events[i:]
            for event in batch:
                yield event
                if event.type in TERMINAL:
                    return
            i += len(batch)


def _default_factory(preset: str) -> Guard:
    return Guard(preset)


def _load_traps(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    keep = ("id", "category", "question", "sources", "notes")
    traps = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            traps.append({k: item[k] for k in keep if k in item})
    return traps


def _load_results(results_dir: Path) -> list[dict[str, Any]]:
    """Benchmark summaries (``meta`` + ``metrics``), newest first. The listing takes no
    user input, and only regular files directly inside ``results_dir`` are read."""
    if not results_dir.is_dir():
        return []
    root = results_dir.resolve()
    found: list[tuple[str, float, dict[str, Any]]] = []
    for path in root.glob("*.json"):
        try:
            if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
                continue
            stat = path.stat()
            if stat.st_size > MAX_RESULT_BYTES:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        meta, metrics = data.get("meta"), data.get("metrics")
        if not isinstance(meta, dict) or not isinstance(metrics, dict):
            continue
        summary = {"name": path.stem, "meta": meta, "metrics": metrics}
        found.append((str(meta.get("date", "")), stat.st_mtime, summary))
    found.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [summary for _, _, summary in found]


def create_app(
    guard_factory: GuardFactory = _default_factory,
    *,
    traps_path: Path = TRAPS_PATH,
    web_dist: Path | None = WEB_DIST,
    results_dir: Path = RESULTS_DIR,
) -> FastAPI:
    app = FastAPI(title="sAIfe", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    runs: dict[str, RunState] = {}

    @app.middleware("http")
    async def limit_body(request: Request, call_next: Callable[[Request], Any]) -> Response:
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        response: Response = await call_next(request)
        return response

    async def execute(state: RunState, req: RunRequest) -> None:
        sources = [Source(**s.model_dump()) for s in req.sources] or None
        try:
            guard = guard_factory(req.preset)
            if req.answer and req.answer.strip():
                state.report = await guard.check(
                    req.answer,
                    sources=sources,
                    question=req.question,
                    run_id=state.run_id,
                    extra_sinks=[state.add],
                )
            else:
                assert req.question is not None
                state.report = await guard.run(
                    req.question, sources=sources, run_id=state.run_id, extra_sinks=[state.add]
                )
        except Exception as exc:  # the stream must still terminate (fail closed)
            log.exception("run %s failed", state.run_id)
            if not state.done:
                seq = state.events[-1].seq + 1 if state.events else 0
                await state.add(
                    Event(
                        type="run.failed",
                        run_id=state.run_id,
                        seq=seq,
                        ts=time.time(),
                        data={"error": f"{type(exc).__name__}: {exc}"},
                    )
                )

    def get_state(run_id: str) -> RunState:
        state = runs.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="unknown run")
        return state

    @app.post("/api/runs", status_code=201)
    async def create_run(req: RunRequest) -> dict[str, str]:
        active = sum(1 for s in runs.values() if not s.done)
        if active >= MAX_ACTIVE_RUNS:
            raise HTTPException(status_code=429, detail="too many active runs; try again shortly")
        finished = [rid for rid, s in runs.items() if s.done]  # oldest first (dict order)
        while len(runs) >= MAX_KEPT_RUNS and finished:
            del runs[finished.pop(0)]
        state = RunState(run_id=uuid.uuid4().hex[:12])
        runs[state.run_id] = state
        state.task = asyncio.create_task(execute(state, req))
        return {"run_id": state.run_id}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str) -> EventSourceResponse:
        state = get_state(run_id)

        async def gen() -> AsyncIterator[ServerSentEvent]:
            async for event in state.stream():
                yield ServerSentEvent(
                    data=event.model_dump_json(), event=event.type, id=str(event.seq)
                )

        return EventSourceResponse(gen(), ping=15)

    @app.get("/api/runs/{run_id}")
    async def run_report(run_id: str) -> Report:
        state = get_state(run_id)
        if state.report is None:
            raise HTTPException(status_code=404, detail="report not ready")
        return state.report

    @app.get("/api/presets")
    async def presets() -> dict[str, Any]:
        return {
            name: cfg.model_dump(mode="json", exclude={"roles"}) for name, cfg in PRESETS.items()
        }

    @app.get("/api/traps")
    async def traps() -> list[dict[str, Any]]:
        return _load_traps(traps_path)

    @app.get("/api/results")
    def results() -> list[dict[str, Any]]:
        return _load_results(results_dir)

    if web_dist is not None and (web_dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


app = create_app()
