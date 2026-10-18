"""The orchestrator: GENERATE → DECOMPOSE → VERIFY → AGGREGATE → DELIVER | REPAIR → … (P4).

Every pass decomposes the current draft, verifies only claims whose IDs are new
(delta, F6), aggregates, and either delivers or repairs the flagged claims. The
loop stops on the first of: nothing left to repair, repair disabled, the pass cap,
the token budget (checked before each stage), the overall timeout, the flagged set
not strictly shrinking (convergence), or a failed repair.

Fail-closed rules held here, not just in the detectors:

- every detector call is wrapped, so an unexpected exception yields UNVERIFIABLE;
- every verdict passes ``enforce_evidence`` again;
- claims the run never got to verify (budget, timeout) are UNVERIFIABLE;
- an edited draft that could not be re-checked is never reported as VERIFIED;
- exactly one ``run.completed`` or ``run.failed`` event ends every run.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from saife.config import VERIFIER, GuardConfig
from saife.detectors.base import Detector, DetectorContext, enforce_evidence, unverifiable
from saife.errors import ConfigError
from saife.events import EventBus
from saife.llm.router import Router
from saife.pipeline.aggregate import (
    aggregate_claim,
    answer_status,
    build_alerts,
    is_factual,
    needs_repair,
)
from saife.pipeline.decompose import decompose
from saife.pipeline.generate import generate
from saife.pipeline.repair import RepairOutcome, repair
from saife.redact import redact
from saife.types import (
    Alert,
    AnswerStatus,
    Claim,
    ClaimResult,
    DetectorVerdict,
    PassRecord,
    RepairOp,
    RepairRecord,
    Report,
    RunUsage,
    Source,
    StopCause,
)

log = logging.getLogger(__name__)

GenerateFn = Callable[[str, DetectorContext], Awaitable[str]]
DecomposeFn = Callable[[str, DetectorContext], Awaitable[list[Claim]]]
RepairFn = Callable[[str, list[ClaimResult], DetectorContext], Awaitable[RepairOutcome]]

# --- Cost estimate ---------------------------------------------------------------------
# USD per million tokens (input, output), Anthropic first-party list prices. This is an
# estimate: it ignores prompt caching, thinking-token detail and partner pricing. The
# verifier gateway prices its own models, so its price comes from the environment.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-5": (5.0, 25.0),
    # The same models through OpenRouter, which passes provider prices through. Other
    # OpenRouter models (":free" ones included) are not listed and count as 0.
    "anthropic/claude-haiku-4.5": (1.0, 5.0),
    "anthropic/claude-opus-5": (5.0, 25.0),
}


def _env_price(name: str) -> float:
    raw = os.getenv(name)
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        log.warning("ignoring %s=%r: not a number", name, raw)
        return 0.0


def verifier_price() -> tuple[float, float]:
    """(input, output) USD per Mtok from ``SAIFE_VERIFIER_PRICE_IN/OUT``; default 0."""
    return _env_price("SAIFE_VERIFIER_PRICE_IN"), _env_price("SAIFE_VERIFIER_PRICE_OUT")


def estimate_cost(usage: RunUsage) -> float:
    total = 0.0
    for key, u in usage.by_model.items():
        provider, _, model = key.partition("/")
        if provider == VERIFIER:
            price_in, price_out = verifier_price()
        else:
            price_in, price_out = PRICES_PER_MTOK.get(model, (0.0, 0.0))
        total += (u.input_tokens * price_in + u.output_tokens * price_out) / 1_000_000
    return round(total, 6)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def abstention(results: Sequence[ClaimResult], risk: float, config: GuardConfig) -> str:
    """BLOCKED answers are replaced with this (P6); the draft stays in the report."""
    n = sum(r.status == "CONTRADICTED" for r in results)
    if n:
        what = "1 claim was" if n == 1 else f"{n} claims were"
        reason = f"{what} contradicted by the evidence and could not be repaired"
    else:
        reason = (
            f"its residual risk ({risk:.2f}) is above the {config.preset} preset's "
            f"threshold ({config.block_risk_threshold:.2f})"
        )
    return (
        f"sAIfe withheld this answer because {reason}. The original draft, the evidence "
        "and every flagged claim are kept in the report; check them against the sources "
        "before relying on any part of it."
    )


NOTHING_VERIFIED = Alert(
    severity="warning",
    message="Nothing in this answer could be verified because claim extraction failed, so "
    "it is shown unchanged and unchecked. Verify it independently before relying on it.",
)
EDITS_UNCHECKED = Alert(
    severity="warning",
    message="The answer was edited to fix flagged claims, but the run stopped before the "
    "edited text could be re-checked. The edits are listed in the report; verify them "
    "before relying on the answer.",
)


@dataclass
class _Run:
    ctx: DetectorContext
    original: str = ""
    draft: str = ""
    claims: dict[str, Claim] = field(default_factory=dict)  # current draft's claims, in order
    results: dict[str, ClaimResult] = field(default_factory=dict)  # every claim ever seen
    passes: list[PassRecord] = field(default_factory=list)
    repairs: list[RepairRecord] = field(default_factory=list)
    notes: list[Alert] = field(default_factory=list)  # loop-level alerts
    decomposed: bool = False  # at least one decomposition succeeded
    stale: bool = False  # the draft was repaired but not yet re-decomposed
    # Output spans of hedge/correct edits in the current draft: already disclosed or fixed.
    disclosed: list[tuple[int, int]] = field(default_factory=list)
    seen_pass: dict[str, int] = field(default_factory=dict)  # last pass each claim was in
    stop: StopCause | None = None


class Pipeline:
    """One guarded run per ``run()`` call. Collaborators are injectable for tests.

    A ``bus`` passed in serves exactly one run (it closes on the terminal event);
    without one, each run gets a fresh ``EventBus``.
    """

    def __init__(
        self,
        router: Router,
        config: GuardConfig,
        *,
        detectors: Sequence[Detector],
        decompose_fn: DecomposeFn = decompose,
        repair_fn: RepairFn = repair,
        generate_fn: GenerateFn = generate,
        bus: EventBus | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.router = router
        self.config = config
        self.detectors = list(detectors)
        self.decompose_fn = decompose_fn
        self.repair_fn = repair_fn
        self.generate_fn = generate_fn
        self.bus = bus
        self.clock = clock

    def _run_router(self) -> Router:
        """A per-run view of the router: shared clients and semaphores, but this config's
        fallback policy and roles, and fallbacks recorded for this run only."""
        r = copy.copy(self.router)
        r.policy = self.config.fallback_policy
        r.fallbacks = []
        if self.config.roles is not None:
            missing = {ref.provider for ref in self.config.roles.values()} - set(r.clients)
            if missing:
                raise ConfigError(f"no client configured for provider(s): {sorted(missing)}")
            r.roles = dict(self.config.roles)
        return r

    async def run(
        self,
        *,
        answer: str | None = None,
        question: str | None = None,
        sources: Sequence[Source] | None = None,
        run_id: str | None = None,
        trace_path: str | None = None,
    ) -> Report:
        if answer is None and not question:
            raise ValueError("need an answer to check or a question to answer")
        bus = self.bus or EventBus(run_id or new_run_id())
        src = list(sources or [])
        st = _Run(
            ctx=DetectorContext(
                draft=answer or "",
                config=self.config,
                router=self._run_router(),
                question=question,
                sources=src,
            )
        )
        started = self.clock()
        terminal = False
        try:
            await bus.emit(
                "run.started",
                question=question,
                preset=self.config.preset,
                mode="answer" if answer is not None else "generate",
                sources=[s.id for s in src],
            )
            timer = asyncio.timeout(self.config.budgets.timeout_s)
            try:
                async with timer:
                    if answer is None:
                        assert question is not None
                        if self._over_budget(st):
                            raise RuntimeError("token budget exhausted before generation")
                        answer = await self.generate_fn(question, st.ctx)
                    st.original = st.draft = st.ctx.draft = answer
                    await self._passes(st, bus)
            except TimeoutError:
                if not timer.expired() or not st.original:
                    raise  # not our timeout, or generation itself timed out
                st.stop = "timeout"
            report = await self._finish(st, bus, started, trace_path)
            terminal = True
            await bus.emit("run.completed", status=report.status, report=report)
            return report
        finally:
            if not terminal:
                exc = sys.exc_info()[1]
                error = f"{type(exc).__name__}: {exc}" if exc else "run aborted"
                try:
                    await bus.emit("run.failed", error=redact(error))
                except Exception:  # never mask the original failure
                    log.exception("could not emit run.failed")

    # --- passes ------------------------------------------------------------------------

    def _over_budget(self, st: _Run) -> bool:
        return st.ctx.usage.total_tokens >= self.config.budgets.max_tokens

    async def _passes(self, st: _Run, bus: EventBus) -> None:
        ctx, cfg = st.ctx, self.config
        prev_flagged: int | None = None
        index = 0
        while True:
            if self._over_budget(st):
                st.stop = "token_budget"
                return
            ctx.pass_index, ctx.draft = index, st.draft
            rec = PassRecord(
                index=index, draft=st.draft, claim_ids=[], verified_ids=[], flagged_ids=[]
            )
            st.passes.append(rec)
            t0 = self.clock()
            try:
                await bus.emit("pass.started", index=index, draft=st.draft)
                try:
                    claims = await self.decompose_fn(st.draft, ctx)
                except Exception as exc:
                    log.warning("decompose failed: %s", redact(f"{type(exc).__name__}: {exc}"))
                    if not st.decomposed:
                        st.notes.append(NOTHING_VERIFIED)
                    st.stop = "decompose_failed"
                    return
                fresh = await self._merge(st, claims, bus)
                rec.claim_ids = list(st.claims)

                to_verify = [c for c in fresh if c.checkworthy and c.span is not None]
                for c in fresh:
                    if c not in to_verify:  # not factual or not locatable: rule 1
                        await self._set(st, aggregate_claim(c, [], pass_index=index), bus)
                if to_verify:
                    if self._over_budget(st):
                        st.stop = "token_budget"
                        return  # _finish marks them UNVERIFIABLE
                    rec.verified_ids = [c.id for c in to_verify]
                    verdicts = await self._verify(to_verify, ctx)
                    for c in to_verify:
                        result = aggregate_claim(c, verdicts[c.id], pass_index=index)
                        await self._set(st, result, bus)

                # Flagged by status even when repair is off, so "repair_disabled" is reported
                # (needs_repair() is False whenever the preset has no repair).
                # Claims lying wholly inside text an earlier hedge/correct wrote are not
                # repaired again (they would only be re-hedged); they keep their status.
                flagged = [
                    st.results[i]
                    for i, c in st.claims.items()
                    if (
                        needs_repair(st.results[i], cfg)
                        or st.results[i].status in cfg.repair_statuses
                    )
                    and not _inside(c.span, st.disclosed)
                ]
                rec.flagged_ids = [r.claim.id for r in flagged]
                if not flagged:
                    st.stop = "resolved"
                    return
                if not cfg.repair:
                    st.stop = "repair_disabled"
                    return
                if index + 1 >= cfg.budgets.max_passes:
                    st.stop = "max_passes"
                    return
                # Convergence: the flagged count must strictly drop pass over pass. Counted,
                # not compared as sets, because rewritten claims get new IDs.
                if prev_flagged is not None and len(flagged) >= prev_flagged:
                    st.stop = "not_converging"
                    return
                prev_flagged = len(flagged)
                if self._over_budget(st):
                    st.stop = "token_budget"
                    return
                try:
                    outcome = await self.repair_fn(st.draft, flagged, ctx)
                except Exception as exc:
                    log.warning("repair failed: %s", redact(f"{type(exc).__name__}: {exc}"))
                    st.stop = "repair_failed"
                    return
                if not outcome.records or outcome.draft == st.draft:
                    st.stop = "not_converging"  # repair changed nothing
                    return
                for r in outcome.records:
                    rec.repairs.append(r)
                    st.repairs.append(r)
                    await bus.emit(
                        "repair.applied", pass_index=index, repair=r, draft=outcome.draft
                    )
                st.disclosed = _carry_disclosed(st.draft, outcome, st.disclosed)
                st.draft = outcome.draft
                st.stale = True
            finally:
                rec.duration_s = self.clock() - t0
            index += 1

    async def _merge(self, st: _Run, claims: list[Claim], bus: EventBus) -> list[Claim]:
        """Adopt a new decomposition; return the claims whose IDs were never seen.

        Known claims keep their result; a moved span is updated (and re-emitted, so the
        trace's last verdict per claim always equals the report)."""
        unique: list[Claim] = []
        for c in claims:
            if c.id not in {u.id for u in unique}:
                unique.append(c)
        st.claims = {c.id: c for c in unique}
        st.stale = False
        st.decomposed = True
        for c in unique:
            st.seen_pass[c.id] = st.ctx.pass_index
            await bus.emit("claim.extracted", pass_index=st.ctx.pass_index, claim=c)
        fresh: list[Claim] = []
        for c in unique:
            old = st.results.get(c.id)
            if old is None:
                fresh.append(c)
            elif old.claim != c:
                await self._set(st, old.model_copy(update={"claim": c}), bus)
        return fresh

    async def _set(self, st: _Run, result: ClaimResult, bus: EventBus) -> None:
        st.results[result.claim.id] = result
        await bus.emit("claim.verdict", pass_index=st.ctx.pass_index, result=result)

    # --- verify ------------------------------------------------------------------------

    async def _verify(
        self, claims: list[Claim], ctx: DetectorContext
    ) -> dict[str, list[DetectorVerdict]]:
        runs: list[Awaitable[list[DetectorVerdict]]] = []
        for d in self.detectors:
            try:
                applies = d.applies(ctx)
            except Exception as exc:
                runs.append(_failed(d, claims, exc))
                continue
            if applies:
                runs.append(self._run_detector(d, claims, ctx))
        by_id: dict[str, list[DetectorVerdict]] = {c.id: [] for c in claims}
        for verdicts in await asyncio.gather(*runs):
            for v in verdicts:
                by_id[v.claim_id].append(v)
        return by_id

    async def _run_detector(
        self, d: Detector, claims: list[Claim], ctx: DetectorContext
    ) -> list[DetectorVerdict]:
        """Exactly one verdict per claim, whatever the detector does (P1)."""
        wanted = {c.id for c in claims}
        out: dict[str, DetectorVerdict] = {}
        try:
            got = await d.check(list(claims), ctx)
            for v in got:  # malformed output (None, non-verdicts) counts as a failure too
                if not isinstance(v, DetectorVerdict):
                    raise TypeError(f"expected DetectorVerdict, got {type(v).__name__}")
                if v.claim_id in wanted and v.claim_id not in out:
                    out[v.claim_id] = enforce_evidence(v)
        except Exception as exc:
            return await _failed(d, claims, exc)
        return [
            out.get(c.id) or unverifiable(d.name, c, f"{d.name} returned no verdict")
            for c in claims
        ]

    # --- deliver -----------------------------------------------------------------------

    async def _finish(
        self, st: _Run, bus: EventBus, started: float, trace_path: str | None
    ) -> Report:
        ctx, cfg = st.ctx, self.config
        reason = {
            "timeout": "run timed out before this claim was verified",
            "token_budget": "token budget exhausted before this claim was verified",
        }.get(st.stop or "", "run stopped before this claim was verified")
        for c in st.claims.values():
            if c.id not in st.results:
                verdicts = [unverifiable(d.name, c, reason) for d in self.detectors]
                await self._set(st, aggregate_claim(c, verdicts, pass_index=ctx.pass_index), bus)
        if st.stale:
            await self._relocate(st, bus)
            st.notes.append(EDITS_UNCHECKED)

        current = [st.results[i] for i in st.claims]
        vanished = [r for i, r in st.results.items() if i not in st.claims]
        unchecked = st.stale or not st.decomposed
        status: AnswerStatus
        status, risk = answer_status(
            current,
            cfg,
            budget_exhausted=unchecked or st.stop in ("token_budget", "timeout"),
        )
        # A hedge discloses an unverified claim instead of removing it, so the answer still
        # contains unverified content: CAUTION, never VERIFIED (PRD §4.3).
        hedged = any(r.op == "hedge" and r.after and r.after in st.draft for r in st.repairs)
        if (unchecked or hedged) and status == "VERIFIED":
            status = "CAUTION"
        stop: StopCause = st.stop or "resolved"
        removed_ids, dropped_ids, covered = self._vanished(st, vanished)
        alerts = [
            *st.notes,
            *build_alerts(
                [*current, *(r for r in vanished if r.claim.id not in covered)],
                st.repairs,
                removed_ids=removed_ids,
                dropped_ids=dropped_ids,
                fallbacks=ctx.fallbacks,
                stop_cause=stop,
            ),
        ]
        for alert in alerts:
            await bus.emit("alert.raised", alert=alert)

        usage = ctx.usage.model_copy(deep=True)
        usage.cost_usd = estimate_cost(usage)
        usage.wall_time_s = round(self.clock() - started, 3)
        return Report(
            run_id=bus.run_id,
            status=status,
            preset=cfg.preset,
            question=ctx.question,
            original_draft=st.original,
            final_answer=abstention(current, risk, cfg) if status == "BLOCKED" else st.draft,
            claims=[*current, *vanished],
            passes=st.passes,
            repairs=st.repairs,
            alerts=alerts,
            usage=usage,
            risk=risk,
            stop_cause=stop,
            verifier_diversity=not ctx.fallbacks,
            fallbacks=list(ctx.fallbacks),
            trace_path=trace_path,
        )

    @staticmethod
    def _vanished(st: _Run, vanished: list[ClaimResult]) -> tuple[list[str], list[str], set[str]]:
        """Sort unresolved claims that left the answer into (removed, dropped, covered).

        Re-decomposing an edited draft gives rewritten text new claim IDs, so a claim can
        vanish because a repair rewrote its span: then it was removed (by a ``remove``
        op) or is covered by the hedge/correct that rewrote it. A claim that vanished
        with no repair touching its span was dropped during re-check."""
        repaired = {r.claim_id for r in st.repairs}
        removed: list[str] = []
        dropped: list[str] = []
        covered: set[str] = set()
        for r in vanished:
            cid = r.claim.id
            if cid in repaired or r.status in ("SUPPORTED", "LIKELY") or not is_factual(r.claim):
                continue
            op = _touching_op(r.claim, st.repairs, st.seen_pass.get(cid))
            if op == "remove":
                removed.append(cid)
            elif op is not None:
                covered.add(cid)
            else:
                dropped.append(cid)
        return removed, dropped, covered

    async def _relocate(self, st: _Run, bus: EventBus) -> None:
        """The draft was repaired but never re-decomposed: repaired claims leave the answer,
        the rest keep their result with spans re-found in the edited draft."""
        repaired = {r.claim_id for r in st.repairs}
        kept: dict[str, Claim] = {}
        for cid, claim in st.claims.items():
            if cid in repaired:
                continue
            i = st.draft.find(claim.anchor) if claim.anchor else -1
            span = (i, i + len(claim.anchor)) if i >= 0 else None
            moved = claim.model_copy(update={"span": span})
            kept[cid] = moved
            old = st.results[cid]
            if old.claim != moved:
                await self._set(st, old.model_copy(update={"claim": moved}), bus)
        st.claims = kept


def _inside(span: tuple[int, int] | None, regions: Sequence[tuple[int, int]]) -> bool:
    """Wholly inside one region; a partial overlap is not enough (fail toward repair)."""
    return span is not None and any(s <= span[0] and span[1] <= e for s, e in regions)


def _carry_disclosed(
    draft: str, outcome: RepairOutcome, disclosed: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Map ``disclosed`` regions through this repair and add its hedge/correct outputs.

    Record spans are in the pre-repair draft and do not overlap. A region an edit touches
    is dropped. If the records do not reproduce the repaired draft the offsets cannot be
    trusted, so nothing is carried (claims become repairable again)."""
    records = sorted(outcome.records, key=lambda r: r.span[0])
    parts: list[str] = []
    pos = 0
    out: list[tuple[int, int]] = []
    for r in records:
        s, e = r.span
        if s < pos or draft[s:e] != r.before:
            return []
        parts.append(draft[pos:s])
        start = sum(map(len, parts))
        parts.append(r.after)
        pos = e
        if r.op in ("hedge", "correct") and r.after:
            out.append((start, start + len(r.after)))
    if "".join(parts) + draft[pos:] != outcome.draft:
        return []
    for ds, de in disclosed:
        if any(ds < r.span[1] and r.span[0] < de for r in records):
            continue
        shift = sum(len(r.after) - len(r.before) for r in records if r.span[1] <= ds)
        out.append((ds + shift, de + shift))
    return sorted(out)


def _touching_op(
    claim: Claim, repairs: Sequence[RepairRecord], pass_index: int | None
) -> RepairOp | None:
    """The op of the repair (in the claim's last pass) that overlaps its span most."""
    if claim.span is None or pass_index is None:
        return None
    cs, ce = claim.span
    best: tuple[int, bool] = (0, False)
    op: RepairOp | None = None
    for r in repairs:
        overlap = min(ce, r.span[1]) - max(cs, r.span[0])
        key = (overlap, r.op == "remove")  # ties go to remove
        if r.pass_index == pass_index and overlap > 0 and key > best:
            best, op = key, r.op
    return op


async def _failed(d: Detector, claims: list[Claim], exc: Exception) -> list[DetectorVerdict]:
    error = redact(f"{d.name} detector failed: {type(exc).__name__}: {exc}")
    log.warning("%s", error)
    return [unverifiable(d.name, c, error) for c in claims]
