"""Benchmark runner (plan Task 5.3): ``saife bench --set seeded|traps ...``.

Each item's ``Report`` is written to ``<out>/<run_name>/items/<id>.json`` as soon as it
finishes, so an interrupted run resumes and skips what is already done. The summary goes
to ``<out>/<run_name>.json`` (and ``.md``); ``GET /api/results`` and the results page
read those files.

The Guard is built by an injectable factory, so tests run the whole runner offline
against ``FakeLLM``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from bench.metrics import Metrics, compute_metrics, flag_rate, is_flagged, percentile
from bench.metrics import silent_failures as find_silent_failures
from bench.report import render_markdown
from bench.schema import SEEDED_PATH, TRAPS_PATH, SeededItem, TrapItem, count_token, load_jsonl
from saife.config import (
    ANTHROPIC,
    ORCHESTRATOR_MODEL,
    VERIFIER_ROLES,
    GuardConfig,
    ModelRef,
    default_roles,
    get_preset,
)
from saife.guard import Guard
from saife.types import Report

RESULTS_DIR = Path(__file__).parent / "results"
TRACES_DIR = Path("runs") / "bench"  # gitignored; traces are large and per-run

SetName = Literal["seeded", "traps"]
VerifierMode = Literal["cross", "same"]
GuardFactory = Callable[[GuardConfig, Path], Guard]
Item = SeededItem | TrapItem
Log = Callable[[str], None]

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ESTIMATE_AFTER = 5  # items run before the cost projection


class CostLimitExceeded(Exception):
    def __init__(self, projected: float, limit: float) -> None:
        super().__init__(f"projected cost ${projected:.2f} exceeds --max-cost ${limit:.2f}")
        self.projected = projected
        self.limit = limit


@dataclass(frozen=True)
class BenchOptions:
    set: SetName = "seeded"
    preset: str = "balanced"
    ablate: tuple[str, ...] = ()
    verifier: VerifierMode = "cross"
    limit: int | None = None
    concurrency: int = 4
    out: Path = RESULTS_DIR
    max_cost: float | None = None
    date: str = field(default_factory=lambda: dt.date.today().isoformat())

    @property
    def run_name(self) -> str:
        name = f"{self.date}-{self.set}-{self.preset}"
        if self.ablate:
            name += "-ablate-" + "-".join(sorted(self.ablate))
        if self.verifier == "same":
            name += "-same"
        return name


class TrapMetrics(BaseModel):
    items: int
    flag_rate: float | None  # share of trap runs with at least one flagged claim (target >= 0.5)
    flag_rate_by_category: dict[str, float]
    silent_failures: int
    silent_failure_ids: list[str] = Field(default_factory=list)
    latency_p50_s: float | None
    latency_p95_s: float | None
    mean_cost_usd: float | None


class BenchMeta(BaseModel):
    set: SetName
    preset: str
    ablate: list[str]
    verifier: VerifierMode
    date: str
    n_items: int  # items scored (errored items excluded)
    n_errors: int  # items whose run raised; not scored, retried on resume
    detectors: list[str]
    models: dict[str, str]  # role -> "provider/model"
    total_cost_usd: float
    run_name: str


class BenchResult(BaseModel):
    meta: BenchMeta
    metrics: Metrics | TrapMetrics
    items: list[dict[str, Any]]


def build_config(opts: BenchOptions) -> GuardConfig:
    """The preset with ablated detectors removed and, for ``same``, every judge on Claude."""
    cfg = get_preset(opts.preset)
    unknown = sorted(set(opts.ablate) - set(cfg.detectors))
    if unknown:
        raise ValueError(f"cannot ablate {unknown}: preset {opts.preset!r} uses {cfg.detectors}")
    if opts.ablate:
        kept = tuple(d for d in cfg.detectors if d not in opts.ablate)
        if not kept:
            raise ValueError("ablation would remove every detector")
        cfg = cfg.with_overrides(detectors=kept)
    if opts.verifier == "same":
        roles = dict(cfg.roles or default_roles())
        claude = ModelRef(provider=ANTHROPIC, model=ORCHESTRATOR_MODEL)
        for role in VERIFIER_ROLES:
            roles[role] = claude
        cfg = cfg.with_overrides(roles=roles)
    return cfg


def load_items(opts: BenchOptions) -> list[Item]:
    items: list[Item] = list(
        load_jsonl(SEEDED_PATH, SeededItem)
        if opts.set == "seeded"
        else load_jsonl(TRAPS_PATH, TrapItem)
    )
    for item in items:
        if not SAFE_ID.match(item.id):  # ids become file names
            raise ValueError(f"unsafe item id {item.id!r}")
    return items[: opts.limit] if opts.limit is not None else items


def default_factory(config: GuardConfig, trace_dir: Path) -> Guard:
    # Items are not streamed to the dashboard one by one; `saife bench` publishes the summary.
    return Guard(config, trace_dir=trace_dir, dashboard=False)


def _load_done(path: Path) -> Report | None:
    try:
        return Report.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None  # missing or half-written: run it again


async def _run_one(guard: Guard, item: Item) -> Report:
    if isinstance(item, SeededItem):
        return await guard.check(item.answer, sources=item.sources)
    return await guard.run(item.question, sources=item.sources)


def _item_summary(item: Item, report: Report | None, error: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": item.id}
    if isinstance(item, SeededItem):
        out.update(clean=item.clean, errors=len(item.errors))
    else:
        out["category"] = item.category
    if report is None:
        out.update(status="ERROR", flagged=False, error=error)
        return out
    out.update(
        status=report.status,
        flagged=any(is_flagged(r.status) for r in report.claims),
        claims=len(report.claims),
        repairs=len(report.repairs),
        silent_failures=len(find_silent_failures([report])),
        cost_usd=report.usage.cost_usd,
        wall_time_s=report.usage.wall_time_s,
        verifier_diversity=report.verifier_diversity,
        run_id=report.run_id,
    )
    if isinstance(item, SeededItem):
        out["residual_before"] = sum(
            count_token(report.original_draft, e.injected) > 0 for e in item.errors
        )
        out["residual_after"] = sum(
            count_token(report.final_answer, e.injected) > 0 for e in item.errors
        )
    return out


def trap_metrics(runs: Sequence[tuple[TrapItem, Report]]) -> TrapMetrics:
    reports = [r for _, r in runs]
    by_cat: dict[str, list[Report]] = {}
    for item, report in runs:
        by_cat.setdefault(item.category, []).append(report)
    silent = find_silent_failures(reports)
    latencies = [r.usage.wall_time_s for r in reports]
    return TrapMetrics(
        items=len(reports),
        flag_rate=flag_rate(reports),
        flag_rate_by_category={c: flag_rate(rs) or 0.0 for c, rs in sorted(by_cat.items())},
        silent_failures=len(silent),
        silent_failure_ids=silent,
        latency_p50_s=percentile(latencies, 50),
        latency_p95_s=percentile(latencies, 95),
        mean_cost_usd=sum(r.usage.cost_usd for r in reports) / len(reports) if reports else None,
    )


async def run_bench(
    opts: BenchOptions,
    factory: GuardFactory = default_factory,
    *,
    items: Sequence[Item] | None = None,
    log: Log = print,
) -> BenchResult:
    """Run (or resume) a benchmark and write its summary JSON and markdown."""
    config = build_config(opts)
    todo_items = list(items) if items is not None else load_items(opts)
    run_dir = opts.out / opts.run_name
    items_dir = run_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    guard = factory(config, TRACES_DIR / opts.run_name)

    reports: dict[str, Report] = {}
    errors: dict[str, str] = {}
    for item in todo_items:
        done = _load_done(items_dir / f"{item.id}.json")
        if done is not None:
            reports[item.id] = done
    pending = [i for i in todo_items if i.id not in reports]
    if reports:
        log(f"resuming {opts.run_name}: {len(reports)} done, {len(pending)} to run")

    sem = asyncio.Semaphore(max(1, opts.concurrency))

    async def one(item: Item) -> None:
        async with sem:
            try:
                report = await _run_one(guard, item)
            except Exception as exc:  # one bad item must not sink the run; it is retried later
                errors[item.id] = f"{type(exc).__name__}: {exc}"
                log(f"  {item.id}: error {errors[item.id]}")
                return
        path = items_dir / f"{item.id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(report.model_dump_json(), encoding="utf-8")
        tmp.replace(path)  # atomic, so a crash never leaves a half-written item
        reports[item.id] = report

    async def batch(group: Sequence[Item]) -> None:
        await asyncio.gather(*(one(i) for i in group))

    first, rest = pending[: min(ESTIMATE_AFTER, len(pending))], pending[ESTIMATE_AFTER:]
    await batch(first)
    if first:
        spent = [reports[i.id].usage.cost_usd for i in first if i.id in reports]
        mean = sum(spent) / len(spent) if spent else 0.0
        projected = sum(spent) + mean * len(rest)
        log(
            f"cost estimate: ~${mean:.4f}/item, ~${projected:.2f} for the {len(pending)} "
            "items in this session (token-price estimate)"
        )
        if opts.max_cost is not None and projected > opts.max_cost:
            raise CostLimitExceeded(projected, opts.max_cost)
    if rest:
        await batch(rest)

    result = _summarise(opts, config, guard, todo_items, reports, errors)
    opts.out.mkdir(parents=True, exist_ok=True)
    (opts.out / f"{opts.run_name}.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    (opts.out / f"{opts.run_name}.md").write_text(render_markdown(result), encoding="utf-8")
    return result


def _summarise(
    opts: BenchOptions,
    config: GuardConfig,
    guard: Guard,
    items: Sequence[Item],
    reports: dict[str, Report],
    errors: dict[str, str],
) -> BenchResult:
    roles = config.roles or guard.router.roles
    metrics: Metrics | TrapMetrics
    if opts.set == "seeded":
        seeded = [i for i in items if isinstance(i, SeededItem) and i.id in reports]
        metrics = compute_metrics([(i, reports[i.id]) for i in seeded])
    else:
        traps = [i for i in items if isinstance(i, TrapItem) and i.id in reports]
        metrics = trap_metrics([(i, reports[i.id]) for i in traps])
    meta = BenchMeta(
        set=opts.set,
        preset=opts.preset,
        ablate=sorted(opts.ablate),
        verifier=opts.verifier,
        date=opts.date,
        n_items=len(reports),
        n_errors=len(errors),
        detectors=list(config.detectors),
        models={str(role): f"{ref.provider}/{ref.model}" for role, ref in sorted(roles.items())},
        total_cost_usd=sum(r.usage.cost_usd for r in reports.values()),
        run_name=opts.run_name,
    )
    summaries = [
        _item_summary(i, reports.get(i.id), errors.get(i.id))
        for i in items
        if i.id in reports or i.id in errors
    ]
    return BenchResult(meta=meta, metrics=metrics, items=summaries)
