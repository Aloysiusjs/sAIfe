"""Fail-closed aggregation of detector verdicts (P1, PRD §4.3).

Pure functions, no I/O. Rules for one claim, first match wins:

1. ``span is None``, not check-worthy, or an opinion → UNVERIFIABLE (not locatable / not factual).
2. Any CONTRADICTED backed by verified evidence (support_kind evidence|computed) → CONTRADICTED.
3. Any SUPPORTED backed by verified evidence → SUPPORTED.
4. Any consistency-type SUPPORTED → LIKELY (agreement alone never proves a claim).
5. Any consistency-type CONTRADICTED, or every detector that ran without error said
   NOT_FOUND (at least one did) → UNSUPPORTED.
6. Otherwise (only errors, UNVERIFIABLE or no verdicts) → UNVERIFIABLE.

Defensively, an evidence/computed SUPPORTED or CONTRADICTED with no *verified* evidence is
read as an errored UNVERIFIABLE, even if a detector forgot ``enforce_evidence``. A computed
SUPPORTED with confidence below 1.0 (a partial recomputation) is ignored as no signal: only
a complete recomputation proves a claim.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from saife.config import GuardConfig
from saife.types import (
    Alert,
    AnswerStatus,
    Claim,
    ClaimKind,
    ClaimResult,
    ClaimStatus,
    DetectorVerdict,
    FallbackRecord,
    RepairRecord,
    Severity,
    StopCause,
)

RISK: dict[ClaimStatus, float] = {
    "CONTRADICTED": 1.0,
    "UNSUPPORTED": 0.6,
    "UNVERIFIABLE": 0.4,
    "LIKELY": 0.2,
    "SUPPORTED": 0.0,
}

_UNRESOLVED: frozenset[ClaimStatus] = frozenset({"CONTRADICTED", "UNSUPPORTED", "UNVERIFIABLE"})


def is_factual(claim: Claim) -> bool:
    """Check-worthy factual claims are the only ones that carry risk."""
    return claim.checkworthy and claim.kind != "opinion"


def _effective(v: DetectorVerdict) -> tuple[str, bool]:
    """(verdict, errored) after the defensive evidence check."""
    if (
        v.verdict in ("SUPPORTED", "CONTRADICTED")
        and v.support_kind != "consistency"
        and not v.has_verified_evidence
    ):
        return "UNVERIFIABLE", True
    return v.verdict, v.error is not None


def _status(claim: Claim, verdicts: Sequence[DetectorVerdict]) -> ClaimStatus:
    if claim.span is None or not is_factual(claim):
        return "UNVERIFIABLE"
    evidence: list[str] = []
    consistency: list[str] = []
    ran_ok: list[str] = []
    for v in verdicts:
        if v.verdict == "SUPPORTED" and v.support_kind == "computed" and v.confidence < 1.0:
            continue
        verdict, errored = _effective(v)
        (consistency if v.support_kind == "consistency" else evidence).append(verdict)
        if not errored:
            ran_ok.append(verdict)
    if "CONTRADICTED" in evidence:
        return "CONTRADICTED"
    if "SUPPORTED" in evidence:
        return "SUPPORTED"
    if "SUPPORTED" in consistency:
        return "LIKELY"
    if "CONTRADICTED" in consistency or (ran_ok and all(x == "NOT_FOUND" for x in ran_ok)):
        return "UNSUPPORTED"
    return "UNVERIFIABLE"


def claim_risk(claim: Claim, status: ClaimStatus) -> float:
    return RISK[status] if is_factual(claim) else 0.0


def aggregate_claim(
    claim: Claim, verdicts: Sequence[DetectorVerdict], *, pass_index: int = 0
) -> ClaimResult:
    status = _status(claim, verdicts)
    return ClaimResult(
        claim=claim,
        status=status,
        verdicts=list(verdicts),
        risk=claim_risk(claim, status),
        pass_index=pass_index,
    )


def needs_repair(result: ClaimResult, config: GuardConfig) -> bool:
    return config.repair and result.status in config.repair_statuses


def answer_status(
    results: Iterable[ClaimResult], config: GuardConfig, *, budget_exhausted: bool = False
) -> tuple[AnswerStatus, float]:
    """Answer-level outcome over the claims that *survive* in the final answer.

    risk = max risk of the factual claims (0 if none).
    BLOCKED: a CONTRADICTED claim survives and either ``block_on_contradicted`` or
    risk > ``block_risk_threshold``. A surviving contradiction has risk 1.0, so it blocks
    under any threshold below 1 (balanced: 1.0 > 0.6 → BLOCKED).
    CAUTION: any factual claim is CONTRADICTED/UNSUPPORTED/UNVERIFIABLE, or the budget ran out.
    VERIFIED: otherwise.
    """
    factual = [r for r in results if is_factual(r.claim)]
    risk = max((r.risk for r in factual), default=0.0)
    contradicted = any(r.status == "CONTRADICTED" for r in factual)
    if contradicted and (config.block_on_contradicted or risk > config.block_risk_threshold):
        return "BLOCKED", risk
    if budget_exhausted or any(r.status in _UNRESOLVED for r in factual):
        return "CAUTION", risk
    return "VERIFIED", risk


# --- Alerts (copy rule, PRD §4.5: what was affected, what sAIfe did, what to do) -----------

_NOUNS: dict[ClaimKind, tuple[str, str]] = {
    "numeric": ("statistic", "statistics"),
    "date": ("date", "dates"),
    "citation": ("citation", "citations"),
    "entity": ("fact", "facts"),
    "causal": ("causal claim", "causal claims"),
    "code_api": ("code reference", "code references"),
    "opinion": ("claim", "claims"),
}


def _subject(kinds: list[ClaimKind | None]) -> tuple[str, bool]:
    """'1 statistic' / '3 claims' and whether the subject is plural."""
    n = len(kinds)
    distinct = set(kinds)
    kind = distinct.pop() if len(distinct) == 1 else None
    singular, plural = _NOUNS[kind] if kind is not None else ("claim", "claims")
    return f"{n} {singular if n == 1 else plural}", n != 1


def _group_alert(
    ids: list[str],
    by_id: dict[str, ClaimResult],
    template: str,
    *,
    severity: Severity | None = None,
) -> Alert:
    kinds = [by_id[i].claim.kind if i in by_id else None for i in ids]
    subject, plural = _subject(kinds)
    words = {
        "subject": subject,
        "was": "were" if plural else "was",
        "is": "are" if plural else "is",
        "it": "them" if plural else "it",
    }
    if severity is None:
        contradicted = any(i in by_id and by_id[i].status == "CONTRADICTED" for i in ids)
        severity = "high" if contradicted else "warning"
    return Alert(severity=severity, claim_ids=ids, message=template.format(**words))


def build_alerts(
    results: Sequence[ClaimResult],
    repairs: Sequence[RepairRecord],
    *,
    removed_ids: Iterable[str],
    dropped_ids: Iterable[str] = (),
    fallbacks: Sequence[FallbackRecord],
    stop_cause: StopCause,
) -> list[Alert]:
    """One alert per outcome group. ``results`` may include removed claims; claims touched
    by a repair are reported under that repair, never again as 'remaining'. ``dropped_ids``
    are claims that left the answer during re-check without any repair touching them."""
    by_id = {r.claim.id: r for r in results}
    removed = set(removed_ids)
    groups: dict[str, list[str]] = {"remove": [], "correct": [], "hedge": []}
    repaired: set[str] = set()
    for rec in repairs:
        if rec.claim_id not in repaired:
            repaired.add(rec.claim_id)
            groups[rec.op].append(rec.claim_id)
    for cid in sorted(removed - repaired):
        repaired.add(cid)
        groups["remove"].append(cid)
    dropped = sorted(set(dropped_ids) - repaired)
    repaired.update(dropped)

    remaining = [r for r in results if r.claim.id not in repaired and is_factual(r.claim)]
    contradicted = [r.claim.id for r in remaining if r.status == "CONTRADICTED"]
    unsupported = [r.claim.id for r in remaining if r.status == "UNSUPPORTED"]
    unverifiable = [r.claim.id for r in remaining if r.status == "UNVERIFIABLE"]

    alerts: list[Alert] = []
    if groups["remove"]:
        bad = any(i in by_id and by_id[i].status == "CONTRADICTED" for i in groups["remove"])
        reason = "contradicted the sources" if bad else "could not be verified"
        alerts.append(
            _group_alert(
                groups["remove"],
                by_id,
                "{subject} " + reason + " and {was} removed. "
                "Check the original source before citing {it}.",
            )
        )
    if groups["correct"]:
        alerts.append(
            _group_alert(
                groups["correct"],
                by_id,
                "{subject} contradicted the sources and {was} corrected using quoted evidence. "
                "Review the changes in the diff before relying on {it}.",
            )
        )
    if groups["hedge"]:
        alerts.append(
            _group_alert(
                groups["hedge"],
                by_id,
                "{subject} could not be confirmed and {was} reworded as uncertain. "
                "Verify {it} before relying on {it}.",
            )
        )
    if dropped:
        alerts.append(
            _group_alert(
                dropped,
                by_id,
                "{subject} from an earlier draft {was} dropped during re-check and {is} no "
                "longer tracked. Compare the original draft with the answer before relying "
                "on {it}.",
            )
        )
    if contradicted:
        alerts.append(
            _group_alert(
                contradicted,
                by_id,
                "{subject} {is} contradicted by the sources and could not be repaired. "
                "Do not rely on {it}; check the cited source.",
                severity="high",
            )
        )
    if unsupported:
        alerts.append(
            _group_alert(
                unsupported,
                by_id,
                "{subject} found no support in the checked sources and {was} left in place. "
                "Verify {it} independently before relying on {it}.",
            )
        )
    if unverifiable:
        alerts.append(
            _group_alert(
                unverifiable,
                by_id,
                "{subject} could not be checked and {is} marked unverified. "
                "Verify {it} independently before relying on {it}.",
            )
        )
    if stop_cause in ("token_budget", "timeout"):
        limit = "token budget" if stop_cause == "token_budget" else "time limit"
        alerts.append(
            Alert(
                severity="warning",
                message=f"The {limit} ran out before every claim was verified, so the remaining "
                "claims are marked unverified. Re-run with a larger budget or check them "
                "yourself.",
            )
        )
    elif stop_cause == "repair_failed":
        alerts.append(
            Alert(
                severity="warning",
                message="The repair step failed, so flagged claims were left unchanged and are "
                "disclosed above. Check them before relying on the answer.",
            )
        )
    if fallbacks:
        n = len(fallbacks)
        checks = "1 verification check" if n == 1 else f"{n} verification checks"
        alerts.append(
            Alert(
                severity="warning" if alerts else "info",
                message=f"The independent verifier was unavailable, so {checks} fell back to "
                f"{fallbacks[0].to_model}, the same provider that wrote the answer. Treat these "
                "results as less independent.",
            )
        )
    return alerts
