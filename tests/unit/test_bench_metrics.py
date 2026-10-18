from __future__ import annotations

import pytest

from bench.metrics import compute_metrics, flag_rate, locate, percentile, silent_failures
from bench.schema import SeededError, SeededItem
from saife.evidence import check_quote
from saife.types import (
    AnswerStatus,
    Claim,
    ClaimResult,
    ClaimStatus,
    DetectorVerdict,
    Report,
    RunUsage,
    Source,
)

SRC = "The tower is 330 metres tall. It opened in 1889. It is made of wrought iron."


def _claim(text: str, anchor: str, answer: str) -> Claim:
    i = answer.index(anchor)
    return Claim.make(text, anchor=anchor, span=(i, i + len(anchor)), kind="entity")


def _verdict(claim: Claim, *, verified: bool = True, error: str | None = None) -> DetectorVerdict:
    quote = "The tower is 330 metres tall" if verified else "not in the source at all"
    ev = check_quote(quote, source_id="s1", source_text=SRC)
    return DetectorVerdict(
        detector="grounding",
        claim_id=claim.id,
        verdict="SUPPORTED",
        confidence=0.9,
        evidence=[ev],
        error=error,
    )


def _result(
    claim: Claim, status: ClaimStatus, *, verified: bool = True, error: str | None = None
) -> ClaimResult:
    verdicts = [_verdict(claim, verified=verified, error=error)] if status == "SUPPORTED" else []
    return ClaimResult(claim=claim, status=status, verdicts=verdicts, risk=0.5)


def _report(
    run_id: str,
    answer: str,
    claims: list[ClaimResult],
    *,
    status: AnswerStatus = "CAUTION",
    final: str | None = None,
    wall: float = 1.0,
    cost: float = 0.01,
) -> Report:
    return Report(
        run_id=run_id,
        status=status,
        preset="balanced",
        original_draft=answer,
        final_answer=answer if final is None else final,
        claims=claims,
        passes=[],
        repairs=[],
        alerts=[],
        usage=RunUsage(wall_time_s=wall, cost_usd=cost),
        risk=0.5,
        stop_cause="resolved",
    )


def _item(item_id: str, answer: str, injected: list[str], *, clean: bool = False) -> SeededItem:
    errors = []
    for text in injected:
        i = answer.index(text)
        errors.append(
            SeededError(type="number_swap", original="x", injected=text, span=(i, i + len(text)))
        )
    return SeededItem(
        id=item_id,
        passage_id=item_id,
        answer=answer,
        sources=[Source(id="s1", text=SRC)],
        clean=clean,
        errors=errors,
    )


def _fixture() -> list[tuple[SeededItem, Report]]:
    """Hand-computed set.

    Item A: errors "450" and "1901" and "steel" (3 errors).
      c1 "450" CONTRADICTED            -> TP
      c2 "1901" UNVERIFIABLE           -> FN (or TP when UNVERIFIABLE counts as flagged)
      "steel" has no claim             -> FN (decomposer miss)
      c3 "Paris" UNSUPPORTED (no err)  -> FP
      c4 "visitors" SUPPORTED          -> TN
      repair claim not in answer       -> unmapped
    Item B: error "Lyon".
      c5 "Lyon" UNSUPPORTED            -> TP
      c6 "white" LIKELY                -> TN
    Item C (clean, status CAUTION): c7 UNSUPPORTED -> FP; c8 SUPPORTED -> TN
    Item D (clean, status VERIFIED): c9 SUPPORTED -> TN

    UNVERIFIABLE not flagged: TP=2 FP=2 FN=2 TN=4 -> P=0.5, R=0.5, F1=0.5
    UNVERIFIABLE flagged:     TP=3 FP=2 FN=1 TN=4 -> P=0.6, R=0.75, F1=2/3
    """
    a = (
        "The tower is 450 metres tall. It opened in 1901. It is made of steel. "
        "It is in Paris. It has visitors."
    )
    ca = [
        _result(_claim("height 450", "450 metres tall", a), "CONTRADICTED"),
        _result(_claim("opened 1901", "opened in 1901", a), "UNVERIFIABLE"),
        _result(_claim("in Paris", "It is in Paris", a), "UNSUPPORTED"),
        _result(_claim("has visitors", "It has visitors", a), "SUPPORTED"),
        ClaimResult(
            claim=Claim.make("repaired", anchor="hedged text", span=(0, 11), kind="entity"),
            status="UNSUPPORTED",
            risk=0.5,
        ),
    ]
    # Guarded answer removed "450" but kept "1901" and "steel".
    ra = _report("a", a, ca, final=a.replace("450 metres", "a tall"), wall=1.0, cost=0.01)
    b = "The museum is in Lyon. Its walls are white and tall today."
    cb = [
        _result(_claim("in Lyon", "in Lyon", b), "UNSUPPORTED"),
        _result(_claim("white walls", "walls are white", b), "LIKELY"),
    ]
    rb = _report(
        "b", b, cb, final="I can't answer that reliably.", status="BLOCKED", wall=2.0, cost=0.03
    )
    c = "The bridge is red. It spans the river."
    cc = [
        _result(_claim("bridge red", "The bridge is red", c), "UNSUPPORTED"),
        _result(_claim("spans river", "It spans the river", c), "SUPPORTED"),
    ]
    rc = _report("c", c, cc, status="CAUTION", wall=3.0, cost=0.02)
    d = "The lake is deep."
    cd = [_result(_claim("lake deep", "The lake is deep", d), "SUPPORTED")]
    rd = _report("d", d, cd, status="VERIFIED", wall=10.0, cost=0.0)
    return [
        (_item("A", a, ["450", "1901", "steel"]), ra),
        (_item("B", b, ["Lyon"]), rb),
        (_item("C", c, [], clean=True), rc),
        (_item("D", d, [], clean=True), rd),
    ]


def test_confusion_matrix_hand_computed() -> None:
    m = compute_metrics(_fixture())
    c = m.confusion
    assert (c.tp, c.fp, c.fn, c.tn) == (2, 2, 2, 4)
    assert m.precision == pytest.approx(0.5)
    assert m.recall == pytest.approx(0.5)
    assert m.f1 == pytest.approx(0.5)
    assert m.errors_total == 4
    assert m.errors_detected == 2
    assert m.unmapped_claims == 1


def test_unverifiable_can_count_as_flagged() -> None:
    m = compute_metrics(_fixture(), unverifiable_flagged=True)
    c = m.confusion
    assert (c.tp, c.fp, c.fn, c.tn) == (3, 2, 1, 4)
    assert (m.precision, m.recall, m.f1) == pytest.approx((0.6, 0.75, 2 / 3))
    assert m.errors_detected == 3


def test_rates_latency_and_cost() -> None:
    m = compute_metrics(_fixture())
    assert m.items == 4 and m.clean_items == 2
    assert m.false_alarm_rate == pytest.approx(0.5)  # C is CAUTION, D is VERIFIED
    assert m.residual_baseline == pytest.approx(1.0)
    # A keeps "1901" and "steel"; B was blocked (abstention text) -> 2 of 4 remain.
    assert m.residual_guarded == pytest.approx(0.5)
    assert m.latency_p50_s == pytest.approx(2.5)
    assert m.latency_p95_s == pytest.approx(3.0 + 0.85 * 7.0)
    assert m.mean_cost_usd == pytest.approx(0.015)
    assert m.silent_failures == 0


def test_empty_inputs_do_not_divide_by_zero() -> None:
    m = compute_metrics([])
    assert m.precision == m.recall == m.f1 == 0.0
    assert m.false_alarm_rate is None and m.residual_guarded is None
    assert m.latency_p50_s is None and m.mean_cost_usd is None
    assert percentile([], 50) is None


def test_silent_failure_detection() -> None:
    text = "The tower is 330 metres tall. It is red. It is blue."
    ok = _claim("height", "330 metres tall", text)
    unbacked = _claim("red", "It is red", text)
    errored = _claim("blue", "It is blue", text)
    report = _report(
        "r1",
        text,
        [
            _result(ok, "SUPPORTED"),
            _result(unbacked, "SUPPORTED", verified=False),
            _result(errored, "SUPPORTED", error="LLMTimeoutError"),
            _result(_claim("x", "It is", text), "UNVERIFIABLE"),
        ],
    )
    found = silent_failures([report])
    assert found == [f"r1:{unbacked.id}", f"r1:{errored.id}"]
    item = _item("r1", text, [])
    assert compute_metrics([(item, report)]).silent_failures == 2


def test_locate_falls_back_to_anchor_search() -> None:
    text = "alpha beta gamma"
    good = Claim.make("b", anchor="beta", span=(6, 10), kind="entity")
    stale = Claim.make("b", anchor="beta", span=(0, 4), kind="entity")
    missing = Claim.make("z", anchor="zeta", span=None, kind="entity")
    assert locate(good, text) == (6, 10)
    assert locate(stale, text) == (6, 10)
    assert locate(missing, text) is None


def test_flag_rate_for_traps() -> None:
    reports = [r for _, r in _fixture()]
    assert flag_rate(reports) == pytest.approx(0.75)  # D has no flagged claim
    assert flag_rate([]) is None
