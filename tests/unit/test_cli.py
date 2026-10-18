"""CLI tests through typer's CliRunner with an injected fake router (no network, no keys)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from typer.testing import CliRunner

from saife import cli
from saife.config import ANTHROPIC, VERIFIER, GuardConfig, default_roles
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse
from saife.llm.router import Router
from saife.types import Report

runner = CliRunner()

SOURCE = "Python was first released in 1991 by Guido van Rossum as a general-purpose language."
ANSWER = "Python was first released in 1991. It was designed by a committee."
QUOTE = "Python was first released in 1991 by Guido van Rossum"


def handler(req: FakeRequest) -> FakeResponse:
    if req.schema_name == "DecomposeOut":
        claims = [
            {
                "text": "Python was first released in 1991.",
                "anchor": "Python was first released in 1991.",
                "kind": "date",
                "checkworthy": True,
            },
            {
                "text": "Python was designed by a committee.",
                "anchor": "It was designed by a committee.",
                "kind": "entity",
                "checkworthy": True,
            },
        ]
        return FakeResponse(text=json.dumps({"claims": claims}))
    if req.schema_name == "GroundingJudgement":
        content = str(req.messages[-1]["content"])
        if "committee" in content.split("</claim>")[0]:
            body = {"verdict": "NOT_FOUND", "confidence": 0.7, "quotes": []}
        else:
            quotes = [{"source_id": "python", "quote": QUOTE}]
            body = {"verdict": "SUPPORTED", "confidence": 0.9, "quotes": quotes}
        return FakeResponse(text=json.dumps(body))
    if req.kind == "complete":
        return FakeResponse(text=ANSWER)
    raise AssertionError(f"unexpected call {req.schema_name}")


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    def make_router(config: GuardConfig) -> Router:
        clients = {ANTHROPIC: FakeLLM(ANTHROPIC, handler=handler)}
        clients[VERIFIER] = FakeLLM(VERIFIER, handler=handler)
        return Router(clients, default_roles(), policy=config.fallback_policy)

    async def resolver(host: str) -> list[str]:
        return ["93.184.216.34"]

    def extras() -> dict[str, Any]:
        transport = httpx2.MockTransport(lambda r: httpx2.Response(200))
        return {"resolver": resolver, "http_client": httpx2.AsyncClient(transport=transport)}

    monkeypatch.setattr(cli, "make_router", make_router)
    monkeypatch.setattr(cli, "guard_kwargs", extras)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answer.md").write_text(ANSWER, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "python.md").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "src" / "ignore.bin").write_bytes(b"\x00")
    return tmp_path


def test_check_renders_table_and_writes_report(fake_env: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "check",
            "--answer",
            "answer.md",
            "--sources",
            "src",
            "--preset",
            "fast",
            "--out",
            "r.json",
        ],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "✓ SUPPORTED" in out
    assert "? UNSUPPORTED" in out
    assert "! CAUTION" in out  # banner shows icon + label, not colour alone
    assert "usage:" in out and "estimate" in out
    report = Report.model_validate_json((fake_env / "r.json").read_text(encoding="utf-8"))
    assert report.status == "CAUTION"
    assert report.preset == "fast"
    assert report.trace_path and Path(report.trace_path).exists()


def test_replay_rebuilds_report(fake_env: Path) -> None:
    runner.invoke(cli.app, ["check", "-a", "answer.md", "-s", "src/python.md", "-p", "fast"])
    report = Report.model_validate_json((fake_env / "report.json").read_text(encoding="utf-8"))
    assert report.trace_path is not None
    result = runner.invoke(cli.app, ["replay", report.trace_path, "--out", "replayed.json"])
    assert result.exit_code == 0, result.output
    replayed = Report.model_validate_json((fake_env / "replayed.json").read_text("utf-8"))
    assert replayed == report


def test_run_generates_with_events(fake_env: Path) -> None:
    result = runner.invoke(
        cli.app, ["run", "When was Python released?", "-s", "src", "-p", "fast", "--events"]
    )
    assert result.exit_code == 0, result.output
    assert "run started" in result.output and "run completed" in result.output
    report = Report.model_validate_json((fake_env / "report.json").read_text(encoding="utf-8"))
    assert report.question == "When was Python released?"
    assert report.original_draft == ANSWER


def test_missing_answer_file_is_an_error(fake_env: Path) -> None:
    result = runner.invoke(cli.app, ["check", "--answer", "nope.md"])
    assert result.exit_code != 0


def test_unknown_preset_is_an_error(fake_env: Path) -> None:
    result = runner.invoke(cli.app, ["check", "-a", "answer.md", "-p", "turbo"])
    assert result.exit_code == 1
    assert "unknown preset" in result.output


def test_replay_of_incomplete_trace_fails(fake_env: Path) -> None:
    trace = fake_env / "bad.jsonl"
    trace.write_text("", encoding="utf-8")
    result = runner.invoke(cli.app, ["replay", str(trace)])
    assert result.exit_code == 1
    assert "error" in result.output


def test_load_sources_dedupes_ids(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "doc.md").write_text("one", encoding="utf-8")
    (tmp_path / "b" / "doc.txt").write_text("two", encoding="utf-8")
    sources = cli.load_sources([tmp_path / "a", tmp_path / "b"])
    assert [s.id for s in sources] == ["doc", "doc-2"]
    assert [s.text for s in sources] == ["one", "two"]


def test_version_and_config_still_work(fake_env: Path) -> None:
    assert runner.invoke(cli.app, ["version"]).exit_code == 0
    result = runner.invoke(cli.app, ["config"])
    assert result.exit_code == 0
    assert "grounding_judge" in result.output
