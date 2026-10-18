from __future__ import annotations

import itertools

import pytest

from saife.config import get_preset
from saife.evidence import check_quote, computed
from saife.pipeline.aggregate import (
    aggregate_claim,
    answer_status,
    build_alerts,
    needs_repair,
)
from saife.types import (
    Claim,
    ClaimKind,
    ClaimResult,
    ClaimStatus,
    DetectorName,
    DetectorVerdict,
    Evidence,
    FallbackRecord,
    RepairRecord,
    SupportKind,
    Verdict,
)

SOURCE = "The Eiffel Tower was completed in 1889 for the World's Fair."
QUOTE = "completed in 1889 for the World's Fair"


def claim(
    text: str = "The Eiffel Tower was completed in 1889.",
    *,
    kind: ClaimKind = "date",
    checkworthy: bool = True,
    span: tuple[int, int] | None = (0, 10),
) -> Claim:
    return Claim.make(text, anchor=text, span=span, kind=kind, checkworthy=checkworthy)


def ev(verified: bool) -> Evidence:
    if verified:
        e = check_quote(QUOTE, source_id="s1", source_text=SOURCE)
        assert e.verified
        return e
    return check_quote("a quote that is not in the source", source_id="s1", source_text=SOURCE)


def vd(
    verdict: Verdict,
    *,
    kind: SupportKind = "evidence",
    verified: bool | None = None,
    error: str | None = None,
    detector: DetectorName = "grounding",
    c: Claim | None = None,
    confidence: float | None = None,
) -> DetectorVerdict:
    if verified is None:
        verified = verdict in ("SUPPORTED", "CONTRADICTED") and kind != "consistency"
    evidence: list[Evidence] = []
    if verified:
        evidence = [computed("arith", "1+1=2") if kind == "computed" else ev(True)]
    elif verdict in ("SUPPORTED", "CONTRADICTED") and kind != "consistency":
        evidence = [ev(False)]
    return DetectorVerdict(
        detector=detector,
        claim_id=(c or claim()).id,
        verdict=verdict,
        confidence=confidence if confidence is not None else (1.0 if kind == "computed" else 0.9),
        support_kind=kind,
        evidence=evidence,
        error=error,
    )


ERR = vd("UNVERIFIABLE", error="LLMUnavailableError: down")
CONS_SUP = vd("SUPPORTED", kind="consistency", detector="consistency")
CONS_CON = vd("CONTRADICTED", kind="consistency", detector="consistency")
CONS_NF = vd("NOT_FOUND", kind="consistency", detector="consistency")
SUP = vd("SUPPORTED")
CON = vd("CONTRADICTED")
SUP_COMPUTED = vd("SUPPORTED", kind="computed", detector="deterministic")
CON_COMPUTED = vd("CONTRADICTED", kind="computed", detector="deterministic")
NF = vd("NOT_FOUND")
UNV = vd("UNVERIFIABLE")  # ran, not applicable, no error
FAKE_SUP = vd("SUPPORTED", verified=False)  # detector forgot enforce_evidence
FAKE_CON = vd("CONTRADICTED", verified=False)
FAKE_SUP_COMPUTED = vd("SUPPORTED", kind="computed", verified=False)
# A partial recomputation (e.g. only one operand checked): verified evidence, confidence < 1.
PARTIAL_COMPUTED = vd("SUPPORTED", kind="computed", detector="deterministic", confidence=0.6)

TABLE: list[tuple[str, list[DetectorVerdict], ClaimStatus]] = [
    ("no verdicts", [], "UNVERIFIABLE"),
    ("only errors", [ERR, ERR], "UNVERIFIABLE"),
    ("only unverifiable", [UNV], "UNVERIFIABLE"),
    ("supported", [SUP], "SUPPORTED"),
    ("computed supported", [SUP_COMPUTED], "SUPPORTED"),
    ("contradicted", [CON], "CONTRADICTED"),
    ("computed contradicted", [CON_COMPUTED], "CONTRADICTED"),
    ("contradiction beats support", [SUP, CON], "CONTRADICTED"),
    ("contradiction beats consistency", [CONS_SUP, CON_COMPUTED], "CONTRADICTED"),
    ("support beats consistency contradiction", [SUP, CONS_CON], "SUPPORTED"),
    ("support with errors", [SUP, ERR], "SUPPORTED"),
    ("support with not found", [SUP, NF], "SUPPORTED"),
    ("consistency only", [CONS_SUP], "LIKELY"),
    ("consistency with not found", [CONS_SUP, NF], "LIKELY"),
    ("consistency with errors", [CONS_SUP, ERR], "LIKELY"),
    ("consistency beats consistency contradiction", [CONS_SUP, CONS_CON], "LIKELY"),
    ("consistency contradiction", [CONS_CON], "UNSUPPORTED"),
    ("consistency contradiction with error", [CONS_CON, ERR], "UNSUPPORTED"),
    ("all not found", [NF, CONS_NF], "UNSUPPORTED"),
    ("not found plus error", [NF, ERR], "UNSUPPORTED"),
    ("not found plus unverifiable", [NF, UNV], "UNVERIFIABLE"),
    ("unverified support", [FAKE_SUP], "UNVERIFIABLE"),
    ("unverified contradiction", [FAKE_CON], "UNVERIFIABLE"),
    ("unverified computed support", [FAKE_SUP_COMPUTED], "UNVERIFIABLE"),
    ("unverified support is an error, not a run", [FAKE_SUP, NF], "UNSUPPORTED"),
    ("unverified support does not beat consistency", [FAKE_SUP, CONS_SUP], "LIKELY"),
    ("unverified contradiction does not block support", [FAKE_CON, SUP], "SUPPORTED"),
    ("partial computed support is no signal", [PARTIAL_COMPUTED], "UNVERIFIABLE"),
    ("partial computed support with not found", [PARTIAL_COMPUTED, NF], "UNSUPPORTED"),
    ("partial computed support with consistency", [PARTIAL_COMPUTED, CONS_SUP], "LIKELY"),
    ("partial computed support with real support", [PARTIAL_COMPUTED, SUP], "SUPPORTED"),
]


@pytest.mark.parametrize(("name", "verdicts", "expected"), TABLE, ids=[t[0] for t in TABLE])
def test_rules_table(name: str, verdicts: list[DetectorVerdict], expected: ClaimStatus) -> None:
    result = aggregate_claim(claim(), verdicts, pass_index=2)
    assert result.status == expected
    assert result.pass_index == 2
    assert result.verdicts == verdicts


@pytest.mark.parametrize(
    "c",
    [
        claim(span=None),
        claim(checkworthy=False),
        claim("I think Paris is lovely.", kind="opinion"),
    ],
    ids=["no span", "not checkworthy", "opinion"],
)
def test_unlocatable_or_nonfactual_is_unverifiable(c: Claim) -> None:
    assert aggregate_claim(c, [SUP, CON]).status == "UNVERIFIABLE"


@pytest.mark.parametrize(
    ("verdicts", "risk"),
    [([CON], 1.0), ([NF], 0.6), ([ERR], 0.4), ([CONS_SUP], 0.2), ([SUP], 0.0)],
)
def test_risk_per_status(verdicts: list[DetectorVerdict], risk: float) -> None:
    assert aggregate_claim(claim(), verdicts).risk == risk


def test_nonfactual_claims_carry_no_risk() -> None:
    assert aggregate_claim(claim(checkworthy=False), []).risk == 0.0
    assert aggregate_claim(claim("I like it.", kind="opinion"), []).risk == 0.0
    assert aggregate_claim(claim(span=None), []).risk == 0.4


VERDICTS: tuple[Verdict, ...] = ("SUPPORTED", "CONTRADICTED", "NOT_FOUND", "UNVERIFIABLE")
KINDS: tuple[SupportKind, ...] = ("evidence", "computed", "consistency")
OPTIONS = [
    vd(v, kind=k, verified=ok, error=err)
    for v, k, ok, err in itertools.product(VERDICTS, KINDS, (False, True), (None, "boom"))
]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_property_no_verified_evidence_never_supported(n: int) -> None:
    """P1/P2: whatever detectors say, no verified evidence → never SUPPORTED/CONTRADICTED."""
    c = claim()
    checked = 0
    for verdicts in itertools.product(OPTIONS, repeat=n):
        status = aggregate_claim(c, verdicts).status
        backed = [
            v for v in verdicts if v.has_verified_evidence and v.support_kind != "consistency"
        ]
        if not backed:
            assert status not in ("SUPPORTED", "CONTRADICTED"), verdicts
        if status == "SUPPORTED":
            assert any(v.verdict == "SUPPORTED" for v in backed), verdicts
        if status == "CONTRADICTED":
            assert any(v.verdict == "CONTRADICTED" for v in backed), verdicts
        checked += 1
    assert checked == len(OPTIONS) ** n


def test_needs_repair_follows_preset() -> None:
    unsupported = aggregate_claim(claim(), [NF])
    contradicted = aggregate_claim(claim(), [CON])
    unverifiable = aggregate_claim(claim(), [ERR])
    balanced = get_preset("balanced")
    assert needs_repair(unsupported, balanced)
    assert needs_repair(contradicted, balanced)
    assert not needs_repair(unverifiable, balanced)
    assert not needs_repair(contradicted, get_preset("fast"))  # fast has no repair


def results(*statuses: list[DetectorVerdict]) -> list[ClaimResult]:
    return [aggregate_claim(claim(f"Claim number {i}."), v) for i, v in enumerate(statuses)]


def test_answer_status_verified() -> None:
    assert answer_status(results([SUP], [CONS_SUP]), get_preset("balanced")) == ("VERIFIED", 0.2)
    assert answer_status([], get_preset("balanced")) == ("VERIFIED", 0.0)


def test_answer_status_ignores_nonfactual() -> None:
    rs = [aggregate_claim(claim("I like it.", kind="opinion"), []), *results([SUP])]
    assert answer_status(rs, get_preset("strict")) == ("VERIFIED", 0.0)


@pytest.mark.parametrize("verdicts", [[NF], [ERR], [CONS_CON]])
def test_answer_status_caution_for_unresolved(verdicts: list[DetectorVerdict]) -> None:
    status, _ = answer_status(results([SUP], verdicts), get_preset("balanced"))
    assert status == "CAUTION"


def test_unlocatable_claim_means_caution() -> None:
    rs = [aggregate_claim(claim(span=None), [SUP]), *results([SUP])]
    assert answer_status(rs, get_preset("balanced")) == ("CAUTION", 0.4)


def test_budget_exhausted_is_at_least_caution() -> None:
    rs = results([SUP])
    assert answer_status(rs, get_preset("balanced"), budget_exhausted=True)[0] == "CAUTION"


def test_surviving_contradiction_blocks_balanced_by_risk() -> None:
    cfg = get_preset("balanced")
    assert not cfg.block_on_contradicted
    assert answer_status(results([SUP], [CON]), cfg) == ("BLOCKED", 1.0)  # 1.0 > 0.6


def test_strict_blocks_on_contradiction_even_with_high_threshold() -> None:
    cfg = get_preset("strict").with_overrides(block_risk_threshold=1.0)
    assert answer_status(results([CON]), cfg)[0] == "BLOCKED"
    relaxed = get_preset("balanced").with_overrides(block_risk_threshold=1.0)
    assert answer_status(results([CON]), relaxed)[0] == "CAUTION"


def test_high_risk_without_contradiction_is_caution() -> None:
    # UNSUPPORTED risk 0.6 > strict threshold 0.4, but nothing contradicted survives.
    assert answer_status(results([NF]), get_preset("strict")) == ("CAUTION", 0.6)


# --- alerts -------------------------------------------------------------------------


def record(c: Claim, op: str, before: str = "x", after: str = "") -> RepairRecord:
    return RepairRecord.model_validate(
        {
            "claim_id": c.id,
            "op": op,
            "before": before,
            "after": after,
            "span": (0, len(before)),
            "reason": "r",
            "pass_index": 0,
        }
    )


FB = FallbackRecord(
    role="grounding_judge",
    from_model="verifier/gemini-3.1-flash-lite",
    to_model="anthropic/claude-opus-5",
    reason="down",
)


def test_alert_copy_rule_for_removed_statistic() -> None:
    stat = claim("Revenue grew 40% in 2023.", kind="numeric")
    rs = [aggregate_claim(stat, [NF])]
    alerts = build_alerts(
        rs, [record(stat, "remove")], removed_ids=[stat.id], fallbacks=[], stop_cause="resolved"
    )
    assert len(alerts) == 1
    assert alerts[0].message == (
        "1 statistic could not be verified and was removed. "
        "Check the original source before citing it."
    )
    assert alerts[0].severity == "warning"
    assert alerts[0].claim_ids == [stat.id]


def test_alerts_group_by_outcome() -> None:
    a = claim("Revenue grew 40%.", kind="numeric")
    b = claim("Profit rose 10%.", kind="numeric")
    corrected = claim("It opened in 1887.", kind="date")
    hedged = claim("Alice founded it.", kind="entity")
    contra = claim("Bob is the CEO.", kind="entity")
    unver = claim("The paper is Smith 2020.", kind="citation")
    unsup = claim("It rained in May.", kind="date")
    rs = [
        aggregate_claim(a, [NF]),
        aggregate_claim(b, [NF]),
        aggregate_claim(corrected, [CON]),
        aggregate_claim(hedged, [NF]),
        aggregate_claim(contra, [CON]),
        aggregate_claim(unver, [ERR]),
        aggregate_claim(unsup, [NF]),
        aggregate_claim(claim("I like it.", kind="opinion"), []),
    ]
    repairs = [record(a, "remove"), record(b, "remove"), record(corrected, "correct", after="y")]
    repairs.append(record(hedged, "hedge", after="y"))
    alerts = build_alerts(
        rs, repairs, removed_ids=[a.id, b.id], fallbacks=[], stop_cause="max_passes"
    )
    by_msg = {al.message: al for al in alerts}
    msgs = list(by_msg)
    assert msgs[0].startswith("2 statistics could not be verified and were removed.")
    assert "Check the original source before citing them." in msgs[0]
    assert msgs[1].startswith("1 date contradicted the sources and was corrected")
    assert msgs[2].startswith("1 fact could not be confirmed and was reworded as uncertain.")
    assert msgs[3].startswith("1 fact is contradicted by the sources and could not be repaired.")
    assert msgs[4].startswith("1 date found no support")
    assert msgs[5].startswith("1 citation could not be checked and is marked unverified.")
    assert len(alerts) == 6
    sev = [al.severity for al in alerts]
    assert sev == ["warning", "high", "warning", "high", "warning", "warning"]
    assert alerts[0].claim_ids == [a.id, b.id]
    assert alerts[3].claim_ids == [contra.id]


def test_mixed_kinds_use_generic_noun() -> None:
    a = claim("Revenue grew 40%.", kind="numeric")
    b = claim("It opened in 1887.", kind="date")
    rs = [aggregate_claim(a, [CON]), aggregate_claim(b, [NF])]
    alerts = build_alerts(
        rs,
        [record(a, "remove"), record(b, "remove")],
        removed_ids=[a.id, b.id],
        fallbacks=[],
        stop_cause="resolved",
    )
    assert alerts[0].message.startswith("2 claims contradicted the sources and were removed.")
    assert alerts[0].severity == "high"


def test_removed_ids_without_records_are_reported() -> None:
    a = claim("Revenue grew 40%.", kind="numeric")
    alerts = build_alerts(
        [aggregate_claim(a, [NF])], [], removed_ids=[a.id], fallbacks=[], stop_cause="resolved"
    )
    assert [al.claim_ids for al in alerts] == [[a.id]]
    assert "removed" in alerts[0].message


def test_fallback_alone_is_info() -> None:
    alerts = build_alerts(
        results([SUP]), [], removed_ids=[], fallbacks=[FB, FB], stop_cause="resolved"
    )
    assert len(alerts) == 1
    assert alerts[0].severity == "info"
    assert "2 verification checks fell back to anthropic/claude-opus-5" in alerts[0].message


def test_fallback_with_other_alerts_is_warning() -> None:
    alerts = build_alerts(results([ERR]), [], removed_ids=[], fallbacks=[FB], stop_cause="resolved")
    assert [al.severity for al in alerts] == ["warning", "warning"]
    assert "1 verification check fell back" in alerts[-1].message


@pytest.mark.parametrize(
    ("cause", "word"), [("token_budget", "token budget"), ("timeout", "time limit")]
)
def test_budget_alert(cause: str, word: str) -> None:
    alerts = build_alerts(
        results([SUP]),
        [],
        removed_ids=[],
        fallbacks=[],
        stop_cause=cause,  # type: ignore[arg-type]
    )
    assert len(alerts) == 1
    assert word in alerts[0].message
    assert alerts[0].severity == "warning"


def test_clean_run_has_no_alerts() -> None:
    rs = results([SUP], [CONS_SUP])
    assert build_alerts(rs, [], removed_ids=[], fallbacks=[], stop_cause="resolved") == []


def test_dropped_claims_get_their_own_alert_not_removed() -> None:
    a = claim("The tower is painted every year.", kind="date")
    b = claim("It was completed in 1901.", kind="date")
    rs = [aggregate_claim(a, [NF]), aggregate_claim(b, [CON])]
    alerts = build_alerts(
        rs,
        [record(b, "correct", after="y")],
        removed_ids=[],
        dropped_ids=[a.id, b.id],  # b is already reported under its repair
        fallbacks=[],
        stop_cause="resolved",
    )
    msgs = [al.message for al in alerts]
    assert len(alerts) == 2
    assert msgs[0].startswith("1 date contradicted the sources and was corrected")
    assert msgs[1].startswith("1 date from an earlier draft was dropped during re-check")
    assert alerts[1].claim_ids == [a.id]
    assert not any("removed" in m for m in msgs)
    assert not any("found no support" in m for m in msgs)
