"""Probe the verifier endpoint once and record what it supports.

Checks: the model is listed at /v1/models, a plain call works, which structured-output
modes are honoured, and the round-trip latency. Writes .saife/verifier_probe.json;
``load_settings`` reads ``best_mode`` from it.

Each mode is tried ``REPS`` times with a nested schema shaped like the real judge output
(a list of quote objects). Some gateways accept ``response_format: json_schema`` without
enforcing it, and a single flat call can pass by luck. So the best mode is the one whose
replies validate on the first attempt most often (ties go to the earlier mode).

Usage:  uv run python scripts/probe_verifier.py
Spends a few cents of verifier credit. Never prints the API key.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from typing import Any, Literal

import openai
from pydantic import BaseModel
from rich.table import Table

from saife.config import PROBE_PATH, STRUCTURED_MODES, load_settings
from saife.errors import ConfigError, LLMError
from saife.llm.openai_compat import OpenAICompatClient
from saife.redact import install_redaction
from saife.term import utf8_console

console = utf8_console()


class ProbeQuote(BaseModel):
    source_id: str
    quote: str


class ProbeVerdict(BaseModel):
    verdict: Literal["SUPPORTED", "CONTRADICTED", "NOT_FOUND"]
    confidence: float
    quotes: list[ProbeQuote]
    rationale: str


REPS = 6
SYSTEM = "You check one claim against the sources. Use only the sources."
PROMPT = (
    '<source id="s1">The Eiffel Tower was completed in 1889 for the World\'s Fair.</source>\n'
    '<source id="s2">It stands on the Champ de Mars in Paris and is 330 metres tall.</source>\n'
    "<claim>The Eiffel Tower was completed in 1889 and stands in Paris.</claim>\n"
    "Return the verdict, confidence between 0 and 1, every exact supporting quote with "
    "its source id, and a one-sentence rationale."
)


async def main() -> int:
    install_redaction()
    try:
        settings = load_settings(probe_path=PROBE_PATH.with_suffix(".none"))
        api_key, base_url = settings.require_verifier()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    model = settings.verifier_model
    report: dict[str, Any] = {
        "probed_at": datetime.now(UTC).isoformat(),
        "host": settings.verifier_host,
        "model": model,
    }
    console.print(f"Probing [bold]{model}[/bold] at [bold]{settings.verifier_host}[/bold]")

    # 1. model listing (some gateways don't implement /models: that's a warning, not a failure)
    raw = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30.0, max_retries=1)
    try:
        listed = [m.id async for m in raw.models.list()]
        report["model_listed"] = model in listed
        if not report["model_listed"]:
            similar = sorted(m for m in listed if "gemini" in m.lower())[:15]
            report["similar_models"] = similar
    except openai.APIError as exc:
        report["model_listed"] = None
        report["models_error"] = f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"

    # 2. plain completion + latency
    client = OpenAICompatClient(api_key=api_key, base_url=base_url, structured_mode="text")
    t0 = time.perf_counter()
    try:
        r = await client.complete(
            model=model,
            system="Reply with one word.",
            messages=[{"role": "user", "content": "Say OK."}],
            max_tokens=16,
        )
        report["plain_call"] = {
            "ok": True,
            "latency_s": round(time.perf_counter() - t0, 2),
            "served_model": r.model,
            "reply": r.text[:40],
        }
    except LLMError as exc:
        report["plain_call"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # 3. each structured-output mode, on a fresh client so downgrades don't leak between modes
    modes: dict[str, Any] = {}
    for mode in STRUCTURED_MODES:
        c = OpenAICompatClient(
            api_key=api_key, base_url=base_url, structured_mode=mode, max_retries=1
        )
        first_try, valid = 0, 0
        latencies: list[float] = []
        sample: dict[str, Any] | None = None
        error: str | None = None
        for _ in range(REPS):
            t0 = time.perf_counter()
            try:
                res = await c.parse(
                    model=model,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": PROMPT}],
                    schema=ProbeVerdict,
                    max_tokens=600,
                )
            except LLMError as exc:
                error = f"{type(exc).__name__}: {exc}"
                continue
            finally:
                latencies.append(time.perf_counter() - t0)
            valid += 1
            first_try += c.last_attempts == 1
            sample = res.parsed.model_dump()
        honoured = c.structured_mode == mode  # False if the client had to downgrade
        modes[mode] = {
            "ok": honoured and valid > 0,
            "downgraded_to": None if honoured else c.structured_mode,
            "first_try_valid": first_try,
            "valid": valid,
            "reps": REPS,
            "latency_s": round(sorted(latencies)[len(latencies) // 2], 2),
            "parsed": sample,
            "error": error,
        }
    report["modes"] = modes
    usable = [m for m in STRUCTURED_MODES if modes[m]["ok"]]
    # max() keeps the first of equal scores, so ties go to the earlier (stricter) mode.
    report["best_mode"] = max(usable, key=lambda m: modes[m]["first_try_valid"], default=None)

    PROBE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROBE_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    table = Table(title="Verifier probe")
    table.add_column("check")
    table.add_column("result")
    table.add_row("model listed", str(report["model_listed"]))
    if report.get("similar_models"):
        table.add_row("similar models", ", ".join(report["similar_models"]))
    pc = report["plain_call"]
    table.add_row("plain call", f"ok, {pc['latency_s']}s" if pc["ok"] else pc["error"])
    for mode_name, res in modes.items():
        if res["ok"]:
            row = (
                f"{res['first_try_valid']}/{res['reps']} valid first try, "
                f"{res['valid']}/{res['reps']} after one retry, p50 {res['latency_s']}s"
            )
        elif res.get("downgraded_to"):
            row = f"rejected (worked only as {res['downgraded_to']})"
        else:
            row = res["error"]
        table.add_row(f"mode: {mode_name}", row)
    table.add_row("best mode", str(report["best_mode"]))
    console.print(table)
    console.print(f"Wrote {PROBE_PATH}")
    return 0 if report["best_mode"] and pc["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
