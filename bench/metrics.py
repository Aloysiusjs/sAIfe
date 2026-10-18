"""Benchmark metrics (plan Task 5.3). Pure functions over ``Report`` objects and dataset items.

Claim-level confusion, with labelled errors as ground truth:

- a claim is *positive* if its anchor overlaps any labelled error span in the seeded answer;
- *flagged* means status CONTRADICTED or UNSUPPORTED (optionally UNVERIFIABLE too);
- TP = flagged positive claims, FP = flagged negative claims, TN = unflagged negative claims;
- FN = unflagged positive claims, plus errors that no claim overlaps at all (the decomposer
  missed them, so the guard never looked; counting them keeps recall honest).

Only claims locatable in the seeded answer (``Report.original_draft`` in check mode) are
scored; claims introduced by repair have no ground truth and are counted as ``unmapped``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field

from bench.schema import SeededItem, count_token
from saife.types import AnswerStatus, Claim, ClaimResult, ClaimStatus, Report

FLAGGED: frozenset[ClaimStatus] = frozenset({"CONTRADICTED", "UNSUPPORTED"})
FALSE_ALARM: frozenset[AnswerStatus] = frozenset({"BLOCKED", "CAUTION"})

Run = tuple[SeededItem, Report]


class Confusion(BaseModel):
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    def __add__(self, other: Confusion) -> Confusion:
        return Confusion(
            tp=self.tp + other.tp,
            fp=self.fp + other.fp,
            tn=self.tn + other.tn,
            fn=self.fn + other.fn,
        )

    @property
    def precision(self) -> float:
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float:
        return _ratio(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)


class Metrics(BaseModel):
    items: int
    clean_items: int
    errors_total: int
    unverifiable_flagged: bool  # whether UNVERIFIABLE counted as flagged
    confusion: Confusion
    precision: float
    recall: float
    f1: float
    errors_detected: int  # labelled errors overlapped by at least one flagged claim
    unmapped_claims: int  # claims not locatable in the seeded answer (not scored)
    false_alarm_rate: float | None  # clean items shown CAUTION/BLOCKED; None if no clean items
    residual_baseline: float | None  # injected errors still in the unguarded draft
    residual_guarded: float | None  # injected errors still in the final answer
    silent_failures: int
    silent_failure_ids: list[str] = Field(default_factory=list)  # "<run_id>:<claim_id>"
    latency_p50_s: float | None
    latency_p95_s: float | None
    mean_cost_usd: float | None


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def is_flagged(status: ClaimStatus, *, unverifiable_flagged: bool = False) -> bool:
    return status in FLAGGED or (unverifiable_flagged and status == "UNVERIFIABLE")


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def locate(claim: Claim, text: str) -> tuple[int, int] | None:
    """The claim's span in ``text``: trust ``claim.span`` only if it slices to the anchor."""
    if claim.span is not None:
        start, end = claim.span
        if 0 <= start <= end <= len(text) and text[start:end] == claim.anchor:
            return claim.span
    if claim.anchor:
        i = text.find(claim.anchor)
        if i >= 0:
            return (i, i + len(claim.anchor))
    return None


def _unique(results: Iterable[ClaimResult]) -> list[ClaimResult]:
    seen: set[str] = set()
    out: list[ClaimResult] = []
    for r in results:
        if r.claim.id not in seen:
            seen.add(r.claim.id)
            out.append(r)
    return out


def score_item(
    item: SeededItem, report: Report, *, unverifiable_flagged: bool = False
) -> tuple[Confusion, int, int]:
    """Confusion counts for one item, plus (errors detected, unmapped claims)."""
    spans = [e.span for e in item.errors]
    hit = [False] * len(spans)  # error overlapped by any claim
    caught = [False] * len(spans)  # error overlapped by a flagged claim
    c = Confusion()
    unmapped = 0
    for r in _unique(report.claims):
        span = locate(r.claim, item.answer)
        if span is None:
            unmapped += 1
            continue
        flagged = is_flagged(r.status, unverifiable_flagged=unverifiable_flagged)
        touched = [i for i, s in enumerate(spans) if _overlaps(span, s)]
        for i in touched:
            hit[i] = True
            caught[i] = caught[i] or flagged
        if touched:
            c.tp += flagged
            c.fn += not flagged
        else:
            c.fp += flagged
            c.tn += not flagged
    c.fn += hit.count(False)
    return c, caught.count(True), unmapped


def residual_rate(runs: Sequence[Run], *, guarded: bool) -> float | None:
    """Share of injected errors whose injected text is still in the answer the user sees."""
    total = present = 0
    for item, report in runs:
        text = report.final_answer if guarded else report.original_draft
        for e in item.errors:
            total += 1
            present += count_token(text, e.injected) > 0
    return present / total if total else None


def silent_failures(reports: Iterable[Report]) -> list[str]:
    """SUPPORTED claims with no verified evidence, or shown SUPPORTED despite a detector error.

    Both break safety rules 1-2; the benchmark must report 0.
    """
    out: list[str] = []
    for report in reports:
        for r in _unique(report.claims):
            if r.status != "SUPPORTED":
                continue
            unbacked = not any(v.has_verified_evidence for v in r.verdicts)
            errored = any(v.error is not None for v in r.verdicts)
            if unbacked or errored:
                out.append(f"{report.run_id}:{r.claim.id}")
    return out


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (numpy's default), ``q`` in [0, 100]."""
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def flag_rate(reports: Sequence[Report], *, unverifiable_flagged: bool = False) -> float | None:
    """Trap-set check (Task 4.3): share of runs with at least one flagged claim."""
    if not reports:
        return None
    n = sum(
        any(is_flagged(r.status, unverifiable_flagged=unverifiable_flagged) for r in rep.claims)
        for rep in reports
    )
    return n / len(reports)


def compute_metrics(runs: Sequence[Run], *, unverifiable_flagged: bool = False) -> Metrics:
    total = Confusion()
    detected = unmapped = 0
    for item, report in runs:
        c, d, u = score_item(item, report, unverifiable_flagged=unverifiable_flagged)
        total += c
        detected += d
        unmapped += u
    clean = [report for item, report in runs if item.clean]
    reports = [report for _, report in runs]
    latencies = [r.usage.wall_time_s for r in reports]
    silent = silent_failures(reports)
    return Metrics(
        items=len(runs),
        clean_items=len(clean),
        errors_total=sum(len(item.errors) for item, _ in runs),
        unverifiable_flagged=unverifiable_flagged,
        confusion=total,
        precision=total.precision,
        recall=total.recall,
        f1=total.f1,
        errors_detected=detected,
        unmapped_claims=unmapped,
        false_alarm_rate=(
            sum(r.status in FALSE_ALARM for r in clean) / len(clean) if clean else None
        ),
        residual_baseline=residual_rate(runs, guarded=False),
        residual_guarded=residual_rate(runs, guarded=True),
        silent_failures=len(silent),
        silent_failure_ids=silent,
        latency_p50_s=percentile(latencies, 50),
        latency_p95_s=percentile(latencies, 95),
        mean_cost_usd=(sum(r.usage.cost_usd for r in reports) / len(reports) if reports else None),
    )
