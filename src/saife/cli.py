"""sAIfe command line: ``check``, ``run``, ``replay`` and ``bench``.

Output follows PRD N5: every status shows an icon and a text label, never colour
alone. Untrusted text (claims, answers) is printed as ``Text``, never parsed as
rich markup.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from saife import __version__
from saife.config import GuardConfig, Settings, get_preset, load_settings
from saife.errors import SaifeError
from saife.guard import Guard
from saife.llm.router import Router
from saife.notify.console import SEVERITY_STYLE, STATUS_STYLE, ConsoleSink, status_label
from saife.term import utf8_console
from saife.types import Report, Source

app = typer.Typer(help="sAIfe: a multi-pass hallucination guard.", no_args_is_help=True)
console = utf8_console()

SOURCE_SUFFIXES = (".md", ".txt", ".markdown", ".rst")


def _no_router(config: GuardConfig) -> Router | None:
    return None


def _no_extras() -> dict[str, Any]:
    return {}


def _dashboard_settings() -> Settings | None:
    """Settings when a dashboard is configured; None (with a note) if it is misconfigured."""
    try:
        settings = load_settings()
    except SaifeError as exc:
        console.print(Text(f"dashboard: skipped ({exc})", style="yellow"))
        return None
    return settings if settings.dashboard_url else None


# Test hooks (monkeypatch them): a Router to use instead of building one from settings
# (None = build), extra ``Guard`` keyword arguments (e.g. a fake ``resolver``), and the
# settings used to publish benchmark results (None = no dashboard).
make_router: Callable[[GuardConfig], Router | None] = _no_router
guard_kwargs: Callable[[], dict[str, Any]] = _no_extras
dashboard_settings: Callable[[], Settings | None] = _dashboard_settings


@app.command()
def version() -> None:
    """Print the sAIfe version."""
    console.print(f"saife {__version__}")


@app.command()
def config() -> None:
    """Show the resolved model routing and verifier settings (keys are never shown)."""
    settings = load_settings()
    console.print(repr(settings))
    for role, ref in settings.roles().items():
        console.print(f"  {role.value:<18} {ref.provider}/{ref.model}")
    host = settings.dashboard_host
    where = f"{host} (events + benchmarks)" if host else "not configured"
    console.print(f"  {'dashboard':<18} {where}")


def load_sources(paths: list[Path] | None) -> list[Source]:
    """Files are used as given; directories contribute their text files, sorted by name."""
    files: list[Path] = []
    for p in paths or []:
        if p.is_dir():
            files.extend(
                sorted(f for f in p.iterdir() if f.is_file() and f.suffix in SOURCE_SUFFIXES)
            )
        elif p.is_file():
            files.append(p)
        else:
            raise typer.BadParameter(f"source not found: {p}", param_hint="--sources")
    sources: list[Source] = []
    used: set[str] = set()
    for f in files:
        sid, n = f.stem, 2
        while sid in used:
            sid, n = f"{f.stem}-{n}", n + 1
        used.add(sid)
        sources.append(Source(id=sid, text=f.read_text(encoding="utf-8"), title=f.name))
    return sources


def _verdicts(report: Report, index: int) -> Text:
    out = Text()
    for v in report.claims[index].verdicts:
        if out:
            out.append(", ")
        out.append(f"{v.detector}:{v.verdict.lower()}")
        if v.fallback:
            out.append(" (fallback)", style="yellow")
    return out


def render(report: Report, out: Path | None = None) -> None:
    current = set(_final_ids(report))
    table = Table(title="Claims", show_lines=False, expand=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Claim", ratio=1)
    table.add_column("Detectors")
    for i, r in enumerate(report.claims):
        claim = Text(r.claim.text)
        if r.claim.id not in current:
            claim.append("  (removed from answer)", style="dim")
        table.add_row(status_label(r.status), r.claim.kind, claim, _verdicts(report, i))
    console.print(table)

    icon, style = STATUS_STYLE.get(report.status, ("•", ""))
    banner = Text(f"{icon} {report.status}", style=style)
    banner.append(
        f"  risk {report.risk:.2f} · stopped: {report.stop_cause} · "
        f"passes {len(report.passes)} · repairs {len(report.repairs)}",
        style="dim",
    )
    if not report.verifier_diversity:
        banner.append("  · verifier fell back (less independent)", style="yellow")
    console.print(Panel(Text(report.final_answer), title=banner, title_align="left"))

    for alert in report.alerts:
        a_icon, a_style = SEVERITY_STYLE.get(alert.severity, ("•", ""))
        line = Text(f"{a_icon} {alert.severity.upper()} ", style=a_style)
        line.append(alert.message)
        console.print(line)

    u = report.usage
    console.print(
        Text(
            f"usage: {u.input_tokens} in / {u.output_tokens} out tokens · {u.calls} calls · "
            f"~${u.cost_usd:.4f} (estimate) · {u.wall_time_s:.1f}s",
            style="dim",
        )
    )
    if report.trace_path:
        console.print(Text(f"trace: {report.trace_path}", style="dim"))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        console.print(Text(f"report: {out}", style="dim"))


def _final_ids(report: Report) -> list[str]:
    """IDs of claims still in the final answer. A last pass that repaired the draft never
    re-decomposed it, so its repaired claims are gone."""
    if not report.passes:
        return []
    last = report.passes[-1]
    repaired = {r.claim_id for r in last.repairs}
    return [i for i in last.claim_ids if i not in repaired]


def _guard(preset: str, trace_dir: Path, events: bool) -> Guard:
    cfg = get_preset(preset)
    sinks = [ConsoleSink(console)] if events else []
    return Guard(cfg, router=make_router(cfg), sinks=sinks, trace_dir=trace_dir, **guard_kwargs())


def _execute(fn: Callable[[], Report], out: Path | None) -> None:
    try:
        report = fn()
    except (SaifeError, ValueError, OSError) as exc:
        console.print(Text(f"error: {type(exc).__name__}: {exc}", style="bold red"))
        raise typer.Exit(1) from exc
    render(report, out)


PresetOpt = Annotated[str, typer.Option("--preset", "-p", help="fast | balanced | strict")]
SourcesOpt = Annotated[
    list[Path] | None, typer.Option("--sources", "-s", help="Source file or directory (repeatable)")
]
OutOpt = Annotated[Path, typer.Option("--out", "-o", help="Where to write report.json")]
TraceOpt = Annotated[Path, typer.Option("--trace-dir", help="Directory for JSONL traces")]
EventsOpt = Annotated[bool, typer.Option("--events", help="Print every event as it happens")]


@app.command()
def check(
    answer: Annotated[Path, typer.Option("--answer", "-a", help="File containing the answer")],
    sources: SourcesOpt = None,
    question: Annotated[
        str | None, typer.Option("--question", "-q", help="Original question")
    ] = None,
    preset: PresetOpt = "balanced",
    out: OutOpt = Path("report.json"),
    trace_dir: TraceOpt = Path("runs"),
    events: EventsOpt = False,
) -> None:
    """Verify an existing answer (bring your own answer)."""
    if not answer.is_file():
        raise typer.BadParameter(f"answer file not found: {answer}", param_hint="--answer")
    text = answer.read_text(encoding="utf-8")
    srcs = load_sources(sources)

    def go() -> Report:
        guard = _guard(preset, trace_dir, events)
        return asyncio.run(guard.check(text, sources=srcs, question=question))

    _execute(go, out)


@app.command()
def run(
    question: Annotated[str, typer.Argument(help="Question for the subject model")],
    sources: SourcesOpt = None,
    preset: PresetOpt = "balanced",
    out: OutOpt = Path("report.json"),
    trace_dir: TraceOpt = Path("runs"),
    events: EventsOpt = False,
) -> None:
    """Generate an answer with the subject model, then verify it."""
    srcs = load_sources(sources)

    def go() -> Report:
        guard = _guard(preset, trace_dir, events)
        return asyncio.run(guard.run(question, sources=srcs))

    _execute(go, out)


@app.command()
def replay(
    trace: Annotated[Path, typer.Argument(help="JSONL trace written by a run")],
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Also write the report here")
    ] = None,
) -> None:
    """Rebuild a report from its trace, without any model calls."""
    from saife.trace import replay as replay_trace

    _execute(lambda: replay_trace(trace), out)


def _bench_module() -> Any:
    """``bench/`` lives at the repo root, outside the installed package: import it from the
    working directory, so ``saife bench`` must run from a checkout."""
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))
    try:
        return importlib.import_module("bench.run")
    except ImportError as exc:
        console.print(Text("error: run `saife bench` from the repository root", style="bold red"))
        raise typer.Exit(1) from exc


@app.command()
def bench(
    set_: Annotated[str, typer.Option("--set", help="seeded | traps")] = "seeded",
    preset: PresetOpt = "balanced",
    ablate: Annotated[
        list[str] | None,
        typer.Option("--ablate", help="Detector to remove from the preset (repeatable)"),
    ] = None,
    verifier: Annotated[
        str, typer.Option("--verifier", help="cross (verifier model) | same (all judges on Claude)")
    ] = "cross",
    limit: Annotated[int | None, typer.Option("--limit", help="Only the first N items")] = None,
    concurrency: Annotated[int, typer.Option("--concurrency", help="Items run at once")] = 4,
    out: Annotated[Path, typer.Option("--out", help="Results directory")] = Path("bench/results"),
    max_cost: Annotated[
        float | None,
        typer.Option("--max-cost", help="Abort if the projected cost (USD) exceeds this"),
    ] = None,
    date: Annotated[
        str | None, typer.Option("--date", help="Run date in the run name (resume an older run)")
    ] = None,
) -> None:
    """Run a benchmark set; resumable. Exits non-zero if any silent failure occurs."""
    if set_ not in ("seeded", "traps"):
        raise typer.BadParameter("must be seeded or traps", param_hint="--set")
    if verifier not in ("cross", "same"):
        raise typer.BadParameter("must be cross or same", param_hint="--verifier")
    mod = _bench_module()
    extra = {"date": date} if date else {}
    opts = mod.BenchOptions(
        set=set_,
        preset=preset,
        ablate=tuple(ablate or ()),
        verifier=verifier,
        limit=limit,
        concurrency=concurrency,
        out=out,
        max_cost=max_cost,
        **extra,
    )

    def factory(cfg: GuardConfig, trace_dir: Path) -> Guard:
        # Bench items are not sent to the dashboard one by one; the summary is published below.
        return Guard(
            cfg, router=make_router(cfg), trace_dir=trace_dir, dashboard=False, **guard_kwargs()
        )

    console.print(Text(f"bench {opts.run_name}", style="bold"))
    try:
        result = asyncio.run(mod.run_bench(opts, factory, log=lambda m: console.print(Text(m))))
    except mod.CostLimitExceeded as exc:
        console.print(Text(f"✗ aborted: {exc}", style="bold red"))
        raise typer.Exit(2) from exc
    except (SaifeError, ValueError, OSError) as exc:
        console.print(Text(f"error: {type(exc).__name__}: {exc}", style="bold red"))
        raise typer.Exit(1) from exc

    m = result.metrics
    silent = m.silent_failures
    icon, style = ("✓", "bold green") if silent == 0 else ("✗", "bold red")
    console.print(
        Panel(
            Text(f"{icon} SILENT FAILURES: {silent}  (target 0)", style=style),
            title="safety check",
            title_align="left",
        )
    )
    for sid in m.silent_failure_ids:
        console.print(Text(f"  {sid}"))
    if result.meta.set == "seeded":
        line = (
            f"P {m.precision:.3f} · R {m.recall:.3f} · F1 {m.f1:.3f} · residual "
            f"{_pct(m.residual_baseline)} → {_pct(m.residual_guarded)} · false alarms "
            f"{_pct(m.false_alarm_rate)}"
        )
    else:
        line = f"trap flag rate {_pct(m.flag_rate)} (target ≥ 50%)"
    console.print(Text(line))
    console.print(
        Text(
            f"items {result.meta.n_items} · errors {result.meta.n_errors} · "
            f"~${result.meta.total_cost_usd:.2f} (estimate) · {out / opts.run_name}.json",
            style="dim",
        )
    )
    dash = dashboard_settings()
    if dash is not None:
        _publish(dash, result.model_dump(mode="json"))
    if silent > 0:
        raise typer.Exit(1)


def _publish(settings: Settings, result: dict[str, Any]) -> bool:
    from saife.notify.dashboard import publish_bench

    try:
        name = asyncio.run(publish_bench(settings, result))
    except SaifeError as exc:
        console.print(Text(f"dashboard: not published ({exc})", style="yellow"))
        return False
    console.print(Text(f"dashboard: published {name} to {settings.dashboard_host}", style="dim"))
    return True


@app.command("publish-bench")
def publish_bench_cmd(
    result: Annotated[Path, typer.Argument(help="A bench/results/<name>.json file")],
) -> None:
    """Send a benchmark result's summary (meta + metrics, not items) to your dashboard."""
    try:
        data = json.loads(result.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        console.print(Text(f"error: cannot read {result}: {exc}", style="bold red"))
        raise typer.Exit(1) from exc
    settings = dashboard_settings()
    if settings is None:
        console.print(
            Text(
                "error: no dashboard configured: set SAIFE_DASHBOARD_URL and "
                "SAIFE_DASHBOARD_SECRET in .env",
                style="bold red",
            )
        )
        raise typer.Exit(1)
    if not _publish(settings, data):
        raise typer.Exit(1)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


if __name__ == "__main__":
    app()
