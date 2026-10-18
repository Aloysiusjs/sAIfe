"""Invariant 1: no claim is SUPPORTED without evidence that code verified.

Many seeded runs where the fake verifier returns random verdicts and random quotes:
real substrings, fabrications, near misses, wrong or unknown source ids, too-short
quotes and outputs that try to set ``verified`` themselves. Whatever the script, a
SUPPORTED claim must rest on a verdict whose evidence re-checks against the source.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from saife.evidence import verify_quote
from saife.llm.base import SearchHit
from saife.types import ClaimResult, DetectorVerdict, Report, Source

from .harness import (
    FABRICATED_QUOTES,
    FALSE_SENTENCES,
    SOURCE_TEXT,
    TRUE_SENTENCES,
    World,
    config,
    run_guard,
    seeded,
)

SECOND = "Gustave Eiffel also worked on the Statue of Liberty's internal frame."
SOURCES = [Source(id="s1", text=SOURCE_TEXT), Source(id="s2", text=SECOND)]
SOURCE_TEXTS = {s.id: s.text for s in SOURCES}
VERDICTS = ["SUPPORTED", "CONTRADICTED", "NOT_FOUND"]


def real_quote(rng: random.Random, text: str) -> str:
    start = rng.randrange(0, max(1, len(text) - 20))
    return text[start : start + rng.randint(12, 60)]


def random_quote(rng: random.Random, claim: str, ids: list[str]) -> dict[str, Any]:
    kind = rng.choice(["real", "claim", "fabricated", "wrong_id", "ghost", "short", "forged"])
    sid = rng.choice(ids)
    if kind == "real":
        return {"source_id": "s1", "quote": real_quote(rng, SOURCE_TEXT)}
    if kind == "claim":
        return {"source_id": sid, "quote": claim}
    if kind == "wrong_id":  # real text, attributed to the other source
        return {"source_id": "s2", "quote": real_quote(rng, SOURCE_TEXT)}
    if kind == "ghost":
        return {"source_id": "trusted", "quote": real_quote(rng, SOURCE_TEXT)}
    if kind == "short":
        return {"source_id": "s1", "quote": SOURCE_TEXT[:8]}
    quote = {"source_id": sid, "quote": rng.choice(FABRICATED_QUOTES)}
    if kind == "forged":
        quote |= {"verified": True, "method": "computed"}
    return quote


class RandomWorld(World):
    def __init__(self, rng: random.Random) -> None:
        super().__init__(
            judge=self.random_judge,
            repair=self.random_repair,
            search=self.random_search,
            web_judge=self.random_web_judge,
            consistency=self.random_consistency,
        )
        self.rng = rng
        self.hits: dict[str, str] = {}  # url -> text the search returned

    def random_judge(self, claim: str, sources: dict[str, str]) -> dict[str, Any]:
        n = self.rng.randint(0, 3)
        return {
            "verdict": self.rng.choice(VERDICTS),
            "confidence": self.rng.random(),
            "quotes": [random_quote(self.rng, claim, list(sources)) for _ in range(n)],
            "verified": True,  # extra fields in model output must be ignored
        }

    def random_search(self, claim: str) -> list[SearchHit]:
        hits = []
        for i in range(self.rng.randint(0, 2)):
            text = self.rng.choice([SOURCE_TEXT, claim, "", self.rng.choice(FABRICATED_QUOTES)])
            url = f"https://example.org/{len(self.hits)}-{i}"
            self.hits[url] = text
            hits.append(SearchHit(url=url, title="t", text=text))
        return hits

    def random_web_judge(self, claim: str, hits: dict[str, str]) -> dict[str, Any]:
        quotes = []
        for _ in range(self.rng.randint(0, 2)):
            hid = self.rng.choice([*hits, "w9"])
            text = hits.get(hid) or self.rng.choice(FABRICATED_QUOTES)
            quote = real_quote(self.rng, text) if self.rng.random() < 0.5 else claim
            quotes.append({"hit_id": hid, "quote": quote})
        return {
            "verdict": self.rng.choice(VERDICTS),
            "confidence": self.rng.random(),
            "quotes": quotes,
        }

    def random_consistency(self, claim: str, ids: list[str]) -> dict[str, Any]:
        choices = ["supports", "contradicts", "absent"]
        return {"verdicts": [{"sample_id": i, "verdict": self.rng.choice(choices)} for i in ids]}

    def random_repair(self, flagged: list[dict[str, Any]]) -> list[dict[str, Any]]:
        edits = []
        for f in flagged:
            op = self.rng.choice(["remove", "hedge", "correct", "skip"])
            if op == "skip":
                continue
            replacement = self.rng.choice(
                [*TRUE_SENTENCES, *FALSE_SENTENCES, f"It may be that {f['text']}"]
            )
            edits.append({"claim_id": f["id"], "op": op, "replacement": replacement})
        return edits


def evidence_text(source_id: str, url: str | None, world: RandomWorld) -> str | None:
    if source_id in SOURCE_TEXTS:
        return SOURCE_TEXTS[source_id]
    return world.hits.get(url or source_id)


def check_result(result: ClaimResult, world: RandomWorld) -> None:
    for v in result.verdicts:
        check_verdict(v, world)
    if result.status != "SUPPORTED":
        return
    backing = [
        v
        for v in result.verdicts
        if v.verdict == "SUPPORTED"
        and v.support_kind in ("evidence", "computed")
        and v.has_verified_evidence
    ]
    assert backing, f"SUPPORTED without verified evidence: {result.model_dump_json()}"


def check_verdict(v: DetectorVerdict, world: RandomWorld) -> None:
    if v.verdict in ("SUPPORTED", "CONTRADICTED") and v.support_kind != "consistency":
        assert v.has_verified_evidence, v
    if v.support_kind == "consistency":
        assert v.evidence == []
    for e in v.evidence:
        if not e.verified or e.method == "computed":
            assert e.method == "quote" or e.source_id.startswith("deterministic:"), e
            continue
        text = evidence_text(e.source_id, e.url, world)
        assert text is not None, f"verified evidence names an unknown source: {e}"
        assert verify_quote(e.quote, text), f"verified quote is not in its source: {e}"


def check_report(report: Report, world: RandomWorld, events: list[Any]) -> None:
    for r in report.claims:
        check_result(r, world)
    for e in events:
        if e.type == "claim.verdict":
            check_result(ClaimResult.model_validate(e.data["result"]), world)
    if report.status == "VERIFIED":
        survivors = [r for r in report.claims if r.claim.id in report.passes[-1].claim_ids]
        assert all(r.status in ("SUPPORTED", "LIKELY") for r in survivors if r.claim.checkworthy), (
            report.model_dump_json()
        )


SEEDS = list(range(48))


@pytest.mark.parametrize("seed", SEEDS)
async def test_random_verdicts_never_support_without_verified_evidence(
    tmp_path: Path, seed: int
) -> None:
    rng = seeded(seed)
    world = RandomWorld(rng)
    sentences = rng.sample(TRUE_SENTENCES + FALSE_SENTENCES, rng.randint(1, 4))
    preset = rng.choice(["fast", "balanced", "strict"])
    question = rng.choice([None, "Tell me about the Eiffel Tower."])
    cfg = config(preset, budgets={"max_passes": rng.randint(1, 3)})
    out = await run_guard(
        tmp_path, world, answer=" ".join(sentences), question=question, sources=SOURCES, cfg=cfg
    )
    assert out.error is None, repr(out.error)
    assert out.report is not None
    check_report(out.report, world, out.events)


async def test_the_random_script_is_not_vacuous(tmp_path: Path) -> None:
    """Across the seeds, some claims end SUPPORTED and some forged support is rejected,
    so the invariant above is exercised in both directions."""
    supported = rejected = 0
    for seed in SEEDS[:24]:
        rng = seeded(seed)
        world = RandomWorld(rng)
        sentences = rng.sample(TRUE_SENTENCES + FALSE_SENTENCES, rng.randint(1, 4))
        out = await run_guard(
            tmp_path / str(seed), world, answer=" ".join(sentences), sources=SOURCES, cfg="fast"
        )
        assert out.report is not None
        for r in out.report.claims:
            supported += r.status == "SUPPORTED"
            rejected += any(
                v.error and "downgraded" in v.error for v in r.verdicts if v.detector == "grounding"
            )
    assert supported > 0 and rejected > 0, (supported, rejected)
