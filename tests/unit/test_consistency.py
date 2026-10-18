from __future__ import annotations

import json
import re
from collections.abc import Callable

import pytest

from saife.config import ANTHROPIC, VERIFIER, FallbackPolicy, Role, default_roles, get_preset
from saife.detectors.base import DetectorContext
from saife.detectors.consistency import ConsistencyDetector, map_ratio
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse
from saife.llm.router import Router
from saife.types import Claim

QUESTION = "When was the Eiffel Tower completed?"
CLAIM = Claim.make("The Eiffel Tower was completed in 1889.", anchor="x", span=None, kind="date")
OTHER = Claim.make("It was designed by Gustave Eiffel.", anchor="y", span=None, kind="entity")

Handler = Callable[[FakeRequest], FakeResponse]


def votes(*vs: str) -> Handler:
    """Judge handler answering ``vs[i]`` for the sample ids it is shown."""

    def handler(req: FakeRequest) -> FakeResponse:
        ids = re.findall(r'<sample id="([^"]+)">', req.messages[-1]["content"])
        payload = {
            "verdicts": [{"sample_id": i, "verdict": v} for i, v in zip(ids, vs, strict=False)],
            "rationale": "compared",
        }
        return FakeResponse(text=json.dumps(payload))

    return handler


def sampler(texts: list[str | None]) -> tuple[Handler, list[FakeRequest]]:
    """Generator handler returning ``texts`` in order; ``None`` simulates an outage."""
    seen: list[FakeRequest] = []

    def handler(req: FakeRequest) -> FakeResponse:
        seen.append(req)
        t = texts[(len(seen) - 1) % len(texts)]
        return FakeResponse(error="LLMUnavailableError") if t is None else FakeResponse(text=t)

    return handler, seen


def make_ctx(
    judge: Handler,
    gen: Handler | None = None,
    *,
    preset: str = "balanced",
    question: str | None = QUESTION,
) -> tuple[DetectorContext, FakeLLM, FakeLLM]:
    gen = gen or sampler(["It was completed in 1889."])[0]
    v = FakeLLM(VERIFIER, handler=judge)
    a = FakeLLM(ANTHROPIC, handler=gen)
    router = Router({ANTHROPIC: a, VERIFIER: v}, default_roles())
    ctx = DetectorContext(
        draft="SECRET DRAFT", config=get_preset(preset), router=router, question=question
    )
    return ctx, v, a


def test_applies_needs_question_and_samples() -> None:
    d = ConsistencyDetector()
    assert d.applies(make_ctx(votes())[0])
    assert not d.applies(make_ctx(votes(), question=None)[0])
    assert not d.applies(make_ctx(votes(), preset="fast")[0])  # consistency_samples=0


@pytest.mark.parametrize(
    ("vs", "expected"),
    [
        (("supports", "supports", "supports"), "SUPPORTED"),
        (("supports", "supports", "absent"), "SUPPORTED"),  # 2/3 counts as ≥0.67
        (("supports", "supports", "contradicts"), "NOT_FOUND"),  # any contradiction blocks
        (("supports", "absent", "absent"), "NOT_FOUND"),
        (("absent", "absent", "absent"), "NOT_FOUND"),
        (("contradicts", "contradicts", "supports"), "CONTRADICTED"),
        (("contradicts", "contradicts", "contradicts"), "CONTRADICTED"),
        (("contradicts", "absent", "absent"), "NOT_FOUND"),
    ],
)
async def test_mapping(vs: tuple[str, ...], expected: str) -> None:
    ctx, _, _ = make_ctx(votes(*vs))
    (v,) = await ConsistencyDetector().check([CLAIM], ctx)
    assert v.verdict == expected
    assert v.detector == "consistency"
    assert v.claim_id == CLAIM.id
    assert v.support_kind == "consistency"
    assert v.evidence == []
    assert not v.has_verified_evidence
    assert 0.0 <= v.confidence <= 1.0


def test_map_ratio_confidence() -> None:
    assert map_ratio(3, 0, 3) == ("SUPPORTED", 1.0)
    assert map_ratio(0, 3, 3) == ("CONTRADICTED", 1.0)
    assert map_ratio(1, 0, 4) == ("NOT_FOUND", 0.75)
    assert map_ratio(2, 2, 4)[0] == "NOT_FOUND"  # tie is not a majority
    assert map_ratio(3, 0, 5)[0] == "NOT_FOUND"  # 0.6 < 2/3


async def test_samples_use_generator_at_temperature_one() -> None:
    gen, seen = sampler(["a1", "a2", "a3"])
    ctx, v, _ = make_ctx(votes("supports", "supports", "supports"), gen)
    await ConsistencyDetector().check([CLAIM], ctx)
    assert len(seen) == ctx.config.consistency_samples == 3
    assert all(r.temperature == 1.0 for r in seen)
    assert all(r.model == default_roles()[Role.GENERATE].model for r in seen)
    content = v.calls[0].messages[-1]["content"]
    for i, text in enumerate(["a1", "a2", "a3"], start=1):
        assert f'<sample id="s{i}">\n{text}\n</sample>' in content


async def test_judge_sees_one_claim_and_no_draft() -> None:
    ctx, v, _ = make_ctx(votes("absent", "absent", "absent"))
    out = await ConsistencyDetector().check([CLAIM, OTHER], ctx)
    assert {x.claim_id for x in out} == {CLAIM.id, OTHER.id}
    assert len(v.calls) == 2
    for req in v.calls:
        content = req.messages[-1]["content"]
        assert content.count("<claim>") == 1
        assert "SECRET DRAFT" not in content
    assert "ignore" in v.calls[0].system.lower()


async def test_samples_cached_per_question() -> None:
    gen, seen = sampler(["a1", "a2", "a3"])
    d = ConsistencyDetector()
    ctx, _, _ = make_ctx(votes("supports", "supports", "supports"), gen)
    await d.check([CLAIM], ctx)
    ctx.pass_index = 1
    await d.check([OTHER], ctx)
    assert len(seen) == 3  # second pass reused the samples
    ctx.question = "A different question?"
    await d.check([CLAIM], ctx)
    assert len(seen) == 6


async def test_sampling_fails_entirely() -> None:
    gen, seen = sampler([None])
    ctx, v, _ = make_ctx(votes("supports", "supports", "supports"), gen)
    d = ConsistencyDetector()
    out = await d.check([CLAIM, OTHER], ctx)
    assert [x.verdict for x in out] == ["UNVERIFIABLE", "UNVERIFIABLE"]
    assert all(x.error and "LLMUnavailableError" in x.error for x in out)
    assert not v.calls
    await d.check([CLAIM], ctx)
    assert len(seen) == 6  # failures are not cached


async def test_partial_sampling_uses_successful_samples() -> None:
    gen, _ = sampler(["a1", None, "a3"])
    ctx, v, _ = make_ctx(votes("supports", "supports"), gen)
    (out,) = await ConsistencyDetector().check([CLAIM], ctx)
    content = v.calls[0].messages[-1]["content"]
    assert content.count("<sample ") == 2
    assert out.verdict == "SUPPORTED"
    assert out.confidence == 1.0


async def test_one_successful_sample_is_not_enough() -> None:
    gen, _ = sampler(["a1", None, None])
    ctx, v, _ = make_ctx(votes("supports"), gen)
    (out,) = await ConsistencyDetector().check([CLAIM], ctx)
    assert out.verdict == "UNVERIFIABLE"
    assert out.error and "1/3" in out.error
    assert not v.calls


async def test_empty_samples_do_not_count() -> None:
    gen, _ = sampler(["a1", "   ", ""])
    ctx, _, _ = make_ctx(votes("supports"), gen)
    (out,) = await ConsistencyDetector().check([CLAIM], ctx)
    assert out.verdict == "UNVERIFIABLE"


@pytest.mark.parametrize("err", ["LLMUnavailableError", "LLMRefusalError", "LLMOutputError"])
async def test_judge_failure_is_unverifiable(err: str) -> None:
    ctx, _, _ = make_ctx(lambda r: FakeResponse(error=err))
    ctx.router.policy = FallbackPolicy.FAIL_CLOSED
    (out,) = await ConsistencyDetector().check([CLAIM], ctx)
    assert out.verdict == "UNVERIFIABLE"
    assert out.error and err in out.error


async def test_missing_unknown_and_duplicate_sample_ids() -> None:
    def judge(req: FakeRequest) -> FakeResponse:
        payload = {
            "verdicts": [
                {"sample_id": "s1", "verdict": "supports"},
                {"sample_id": "s1", "verdict": "supports"},  # duplicate counts once
                {"sample_id": "s9", "verdict": "supports"},  # unknown id ignored
            ]
        }  # s2, s3 missing → absent
        return FakeResponse(text=json.dumps(payload))

    ctx, _, _ = make_ctx(judge)
    (out,) = await ConsistencyDetector().check([CLAIM], ctx)
    assert out.verdict == "NOT_FOUND"


async def test_judge_fallback_propagates() -> None:
    def claude(req: FakeRequest) -> FakeResponse:
        if req.kind == "complete":
            return FakeResponse(text="1889.")
        return votes("supports", "supports", "supports")(req)

    ctx, _, _ = make_ctx(lambda r: FakeResponse(error="LLMUnavailableError"), claude)
    (out,) = await ConsistencyDetector().check([CLAIM], ctx)
    assert out.fallback
    assert out.verdict == "SUPPORTED"
    assert out.evidence == []
    assert ctx.fallbacks and ctx.fallbacks[0].role == Role.CONSISTENCY_JUDGE.value


async def test_never_verified_evidence() -> None:
    """Whatever the judge says, consistency never attaches evidence (let alone verified)."""
    labels = ("supports", "contradicts", "absent")
    combos = [(a, b, c) for a in labels for b in labels for c in labels]
    for combo in combos:
        ctx, _, _ = make_ctx(votes(*combo))
        (out,) = await ConsistencyDetector().check([CLAIM], ctx)
        assert out.evidence == [] and not out.has_verified_evidence, combo
        assert out.support_kind == "consistency", combo
