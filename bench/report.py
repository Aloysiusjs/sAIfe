"""Markdown rendering of a benchmark result (``bench/results/<run_name>.md``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bench.metrics import Metrics

if TYPE_CHECKING:
    from bench.run import BenchResult, TrapMetrics


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _num(x: float | None, fmt: str = ".3f") -> str:
    return "n/a" if x is None else format(x, fmt)


def _seeded(m: Metrics) -> list[str]:
    c = m.confusion
    return [
        "## Residual hallucination rate",
        "",
        "Share of injected errors whose text is still in the answer.",
        "",
        "| Without sAIfe (draft) | With sAIfe (final answer) |",
        "| --- | --- |",
        f"| {_pct(m.residual_baseline)} | {_pct(m.residual_guarded)} |",
        "",
        "## Claim-level detection",
        "",
        "| Precision | Recall | F1 | False-alarm rate | Errors detected |",
        "| --- | --- | --- | --- | --- |",
        f"| {_num(m.precision)} | {_num(m.recall)} | {_num(m.f1)} | "
        f"{_pct(m.false_alarm_rate)} | {m.errors_detected} / {m.errors_total} |",
        "",
        f"Confusion: TP {c.tp} · FP {c.fp} · TN {c.tn} · FN {c.fn}. "
        f"Unmapped claims (not scored): {m.unmapped_claims}. "
        f"Items: {m.items} ({m.clean_items} clean).",
        "",
    ]


def _traps(m: TrapMetrics) -> list[str]:
    lines = [
        "## Trap flag rate",
        "",
        f"Runs with at least one flagged claim: **{_pct(m.flag_rate)}** of {m.items} "
        "(target at least 50%).",
        "",
        "| Category | Flag rate |",
        "| --- | --- |",
    ]
    lines += [f"| {cat} | {_pct(rate)} |" for cat, rate in m.flag_rate_by_category.items()]
    return [*lines, ""]


def render_markdown(result: BenchResult) -> str:
    meta, m = result.meta, result.metrics
    ablate = ", ".join(meta.ablate) or "none"
    lines = [
        f"# sAIfe benchmark: {meta.run_name}",
        "",
        f"- Set: `{meta.set}` · preset: `{meta.preset}` · ablated: {ablate} · "
        f"verifier: {meta.verifier}",
        f"- Detectors: {', '.join(meta.detectors)}",
        f"- Date: {meta.date} · items scored: {meta.n_items} · errored: {meta.n_errors}",
        f"- Total cost (token-price estimate): ${meta.total_cost_usd:.2f}",
        "",
        f"**Silent failures: {m.silent_failures}** (target 0)",
        "",
    ]
    lines += _seeded(m) if isinstance(m, Metrics) else _traps(m)
    lines += [
        "## Latency and cost",
        "",
        "| p50 latency | p95 latency | Mean cost per item |",
        "| --- | --- | --- |",
        f"| {_num(m.latency_p50_s, '.1f')} s | {_num(m.latency_p95_s, '.1f')} s | "
        f"${_num(m.mean_cost_usd, '.4f')} |",
        "",
        "## Models",
        "",
        "| Role | Model |",
        "| --- | --- |",
        *(f"| {role} | {model} |" for role, model in meta.models.items()),
        "",
    ]
    if m.silent_failure_ids:
        lines += ["## Silent failures", "", *(f"- `{i}`" for i in m.silent_failure_ids), ""]
    return "\n".join(lines)
