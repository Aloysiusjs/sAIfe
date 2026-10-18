from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from saife.events import Sink
from saife.guard import Guard
from saife.server.app import create_app
from saife.types import Event, Report, RunUsage, Source


def _report(run_id: str, answer: str) -> Report:
    return Report(
        run_id=run_id,
        status="CAUTION",
        preset="fast",
        original_draft=answer,
        final_answer=answer,
        claims=[],
        passes=[],
        repairs=[],
        alerts=[],
        usage=RunUsage(),
        risk=0.4,
        stop_cause="repair_disabled",
    )


class FakeGuard:
    """Emits a fixed event sequence to the per-run sinks, like the real Guard."""

    def __init__(self, preset: str, *, fail: bool = False) -> None:
        self.preset = preset
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def _emit(self, run_id: str, sinks: Sequence[Sink], types: list[str]) -> None:
        for seq, t in enumerate(types):
            await sinks[0](Event(type=t, run_id=run_id, seq=seq, ts=time.time()))  # type: ignore[arg-type]

    async def check(
        self,
        answer: str,
        sources: list[Source] | None = None,
        question: str | None = None,
        *,
        run_id: str | None = None,
        extra_sinks: Sequence[Sink] = (),
    ) -> Report:
        assert run_id is not None
        self.calls.append({"mode": "check", "answer": answer, "sources": sources})
        if self.fail:
            await self._emit(run_id, extra_sinks, ["run.started"])
            raise RuntimeError("boom")
        report = _report(run_id, answer)
        await self._emit(run_id, extra_sinks, ["run.started", "pass.started", "run.completed"])
        return report

    async def run(
        self,
        question: str,
        sources: list[Source] | None = None,
        *,
        run_id: str | None = None,
        extra_sinks: Sequence[Sink] = (),
    ) -> Report:
        assert run_id is not None
        self.calls.append({"mode": "run", "question": question})
        await self._emit(run_id, extra_sinks, ["run.started", "run.completed"])
        return _report(run_id, "generated")


def _client(tmp_path: Path, **kw: Any) -> tuple[TestClient, list[FakeGuard]]:
    made: list[FakeGuard] = []

    def factory(preset: str) -> Guard:
        g = FakeGuard(preset, **kw)
        made.append(g)
        return cast(Guard, g)

    traps = tmp_path / "traps.jsonl"
    traps.write_text(
        json.dumps({"id": "t1", "category": "citation", "question": "q?", "expected_flag": "x"})
        + "\n",
        encoding="utf-8",
    )
    return TestClient(create_app(factory, traps_path=traps, web_dist=None)), made


def _sse_types(client: TestClient, run_id: str) -> list[str]:
    types = []
    with client.stream("GET", f"/api/runs/{run_id}/events") as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("event:"):
                types.append(line.split(":", 1)[1].strip())
    return types


def test_check_run_streams_events_and_report(tmp_path: Path) -> None:
    client, made = _client(tmp_path)
    with client:
        r = client.post(
            "/api/runs",
            json={
                "answer": "The sky is green.",
                "preset": "fast",
                "sources": [{"id": "s", "text": "t"}],
            },
        )
        assert r.status_code == 201
        run_id = r.json()["run_id"]
        assert _sse_types(client, run_id) == ["run.started", "pass.started", "run.completed"]
        # A late subscriber still gets the full history.
        assert _sse_types(client, run_id) == ["run.started", "pass.started", "run.completed"]
        report = client.get(f"/api/runs/{run_id}").json()
        assert report["run_id"] == run_id and report["final_answer"] == "The sky is green."
    assert made[0].preset == "fast" and made[0].calls[0]["mode"] == "check"
    assert made[0].calls[0]["sources"][0].id == "s"


def test_question_only_uses_generate_mode(tmp_path: Path) -> None:
    client, made = _client(tmp_path)
    with client:
        run_id = client.post("/api/runs", json={"question": "Who?"}).json()["run_id"]
        assert _sse_types(client, run_id)[-1] == "run.completed"
    assert made[0].calls[0] == {"mode": "run", "question": "Who?"}
    assert made[0].preset == "balanced"


def test_guard_crash_still_terminates_stream(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, fail=True)
    with client:
        run_id = client.post("/api/runs", json={"answer": "x"}).json()["run_id"]
        assert _sse_types(client, run_id) == ["run.started", "run.failed"]
        assert client.get(f"/api/runs/{run_id}").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [{}, {"question": "  "}, {"answer": "x", "preset": "yolo"}, {"answer": "x" * 40_001}],
)
def test_invalid_requests_rejected(tmp_path: Path, payload: dict[str, Any]) -> None:
    client, made = _client(tmp_path)
    with client:
        assert client.post("/api/runs", json=payload).status_code == 422
    assert not made


def test_oversized_body_rejected(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        r = client.post(
            "/api/runs",
            content=b"{" + b" " * 1_000_001 + b"}",
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 413


def test_unknown_run_404(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        assert client.get("/api/runs/nope").status_code == 404
        assert client.get("/api/runs/nope/events").status_code == 404


def test_presets_and_traps(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        presets = client.get("/api/presets").json()
        assert set(presets) == {"fast", "balanced", "strict"}
        assert presets["strict"]["block_on_contradicted"] is True
        traps = client.get("/api/traps").json()
        assert traps == [{"id": "t1", "category": "citation", "question": "q?"}]


def test_cors_only_dev_origin(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        ok = client.get("/api/presets", headers={"Origin": "http://localhost:5173"})
        bad = client.get("/api/presets", headers={"Origin": "https://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers
