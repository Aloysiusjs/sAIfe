from __future__ import annotations

import pytest
from pydantic import ValidationError

from saife.config import PRESETS, Budgets, FallbackPolicy, GuardConfig, get_preset
from saife.errors import ConfigError
from saife.evidence import check_quote, computed
from saife.llm.base import Usage
from saife.types import (
    Alert,
    Claim,
    ClaimResult,
    DetectorVerdict,
    Event,
    Evidence,
    FallbackRecord,
    PassRecord,
    RepairRecord,
    Report,
    RunUsage,
    claim_id,
)

SOURCE = "The Eiffel Tower was completed in 1889 for the World's Fair."


def sample_report() -> Report:
    c = Claim.make(
        "The Eiffel Tower was completed in 1889.",
        anchor="completed in 1889",
        span=(20, 37),
        kind="date",
    )
    ev = check_quote("completed in 1889 for the World's Fair", source_id="s1", source_text=SOURCE)
    verdict = DetectorVerdict(
        detector="grounding",
        claim_id=c.id,
        verdict="SUPPORTED",
        confidence=0.8,
        evidence=[ev, computed("date", "1889 < 1900")],
        model="verifier/gemini-3.1-flash-lite",
    )
    result = ClaimResult(claim=c, status="SUPPORTED", verdicts=[verdict], risk=0.0)
    repair = RepairRecord(
        claim_id=c.id,
        op="correct",
        before="1887",
        after="1889",
        span=(33, 37),
        reason="source says 1889",
        evidence=[ev],
        pass_index=0,
    )
    usage = RunUsage()
    usage.add("anthropic", "claude-opus-5", Usage(input_tokens=10, output_tokens=5))
    return Report(
        run_id="r1",
        status="VERIFIED",
        preset="balanced",
        question="When?",
        original_draft="The tower was completed in 1887.",
        final_answer="The tower was completed in 1889.",
        claims=[result],
        passes=[
            PassRecord(
                index=0,
                draft="d",
                claim_ids=[c.id],
                verified_ids=[c.id],
                flagged_ids=[],
                repairs=[repair],
            )
        ],
        repairs=[repair],
        alerts=[Alert(severity="info", claim_ids=[c.id], message="m")],
        usage=usage,
        risk=0.0,
        stop_cause="resolved",
        verifier_diversity=False,
        fallbacks=[
            FallbackRecord(role="grounding_judge", from_model="a", to_model="b", reason="x")
        ],
    )


def test_report_json_round_trip() -> None:
    report = sample_report()
    again = Report.model_validate_json(report.model_dump_json())
    assert again == report
    assert again.claims[0].claim.span == (20, 37)
    assert again.claims[0].verdicts[0].has_verified_evidence
    assert again.usage.by_model["anthropic/claude-opus-5"].output_tokens == 5


def test_event_round_trip() -> None:
    report = sample_report()
    event = Event(
        type="run.completed", run_id="r1", seq=3, ts=1.5, data={"report": report.model_dump()}
    )
    again = Event.model_validate_json(event.model_dump_json())
    assert Report.model_validate(again.data["report"]) == report


@pytest.mark.parametrize(
    "variant",
    [
        "The Eiffel Tower was completed in 1889.",
        "  The  Eiffel Tower\nwas completed\tin 1889.  ",
        "the eiffel tower WAS completed in 1889.",
        "The Eiffel Tower was completed in 1889.",  # non-breaking space
    ],
)
def test_claim_id_stable_under_whitespace_and_case(variant: str) -> None:
    assert claim_id(variant) == claim_id("The Eiffel Tower was completed in 1889.")


def test_claim_id_quote_variants_and_distinct_text() -> None:
    assert claim_id("It’s “here”.") == claim_id('It\'s "here".')
    assert claim_id("Completed in 1889.") != claim_id("Completed in 1887.")
    assert claim_id("x").startswith("c_") and len(claim_id("x")) == 14


def test_claim_make_uses_stable_id() -> None:
    c = Claim.make("A  b.", anchor="A b.", span=None, kind="entity")
    assert c.id == claim_id("a b.")
    assert c.checkworthy
    assert Claim.model_validate_json(c.model_dump_json()) == c


def test_evidence_defaults_to_unverified() -> None:
    e = Evidence(source_id="s1", quote="anything at all here")
    assert not e.verified
    v = DetectorVerdict(
        detector="grounding", claim_id="c", verdict="SUPPORTED", confidence=1.0, evidence=[e]
    )
    assert not v.has_verified_evidence
    assert v.support_kind == "evidence"


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_bounded(confidence: float) -> None:
    with pytest.raises(ValidationError):
        DetectorVerdict(
            detector="grounding", claim_id="c", verdict="NOT_FOUND", confidence=confidence
        )


def test_invalid_literals_rejected() -> None:
    with pytest.raises(ValidationError):
        DetectorVerdict.model_validate(
            {"detector": "grounding", "claim_id": "c", "verdict": "TRUE", "confidence": 1}
        )
    with pytest.raises(ValidationError):
        Claim.make("x", anchor="x", span=None, kind="rumour")  # type: ignore[arg-type]


def test_run_usage_accumulates_per_model() -> None:
    u = RunUsage()
    u.add("anthropic", "m", Usage(input_tokens=3, output_tokens=2))
    u.add("anthropic", "m", Usage(input_tokens=1, output_tokens=1))
    u.add("verifier", "v", Usage(input_tokens=5))
    assert (u.input_tokens, u.output_tokens, u.calls, u.total_tokens) == (9, 3, 3, 12)
    assert u.by_model["anthropic/m"] == Usage(input_tokens=4, output_tokens=3)


# --- presets -------------------------------------------------------------------------


def test_presets_match_prd() -> None:
    fast, balanced, strict = (get_preset(n) for n in ("fast", "balanced", "strict"))
    assert fast.detectors == ("grounding", "deterministic") and not fast.repair
    assert fast.budgets.max_passes == 1
    assert "consistency" in balanced.detectors and balanced.consistency_samples == 3
    assert balanced.budgets.max_passes == 2
    assert balanced.fallback_policy is FallbackPolicy.FALLBACK_AND_DISCLOSE
    assert set(strict.detectors) == {"grounding", "consistency", "web_evidence", "deterministic"}
    assert strict.consistency_samples == 5 and strict.budgets.max_passes == 3
    assert strict.block_on_contradicted
    assert strict.fallback_policy is FallbackPolicy.FAIL_CLOSED


def test_unknown_preset() -> None:
    with pytest.raises(ConfigError):
        get_preset("reckless")


def test_overrides_do_not_mutate_preset() -> None:
    base = get_preset("balanced")
    cfg = base.with_overrides(budgets={"max_passes": 5}, repair=False)
    assert cfg.budgets == Budgets(max_passes=5, max_tokens=400_000, timeout_s=120.0)
    assert not cfg.repair
    assert PRESETS["balanced"].budgets.max_passes == 2 and PRESETS["balanced"].repair


def test_config_is_frozen_and_round_trips() -> None:
    cfg = get_preset("strict")
    with pytest.raises(ValidationError):
        cfg.repair = False  # type: ignore[misc]
    assert GuardConfig.model_validate_json(cfg.model_dump_json()) == cfg
