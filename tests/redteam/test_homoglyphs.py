"""Look-alike quotes: Cyrillic/Greek homoglyphs and zero-width characters must not let a
quote that only *looks* like the source verify against it.

Normalisation (``saife.text.normalize``) drops zero-width characters and applies NFKC,
so a quote whose characters are, after that, exactly the source's is accepted (it is the
same text). Homoglyphs are different letters and are never folded into Latin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from saife.evidence import verify_quote
from saife.types import Source
from tests.invariants.harness import HEIGHT, PARIS, SOURCE_TEXT, World, config, run_guard

ZW = "​"
ZWJ = "‍"
# (name, look-alike of a real source sentence)
LOOKALIKES: list[tuple[str, str]] = [
    ("cyrillic-o", PARIS.replace("o", "о")),
    ("cyrillic-a", PARIS.replace("a", "а", 1)),
    ("cyrillic-e", PARIS.replace("e", "е")),
    ("cyrillic-T", PARIS.replace("T", "Т", 1)),
    ("cyrillic-i", PARIS.replace("i", "і")),
    ("greek-omicron", HEIGHT.replace("o", "ο")),
    ("greek-Alpha-capital", "Α" + PARIS[1:].replace("T", "Τ")),
    ("cyrillic+zero-width", ZW.join(PARIS.replace("a", "а"))),
    ("zero-width-joiner+cyrillic", HEIGHT.replace("3", ZWJ + "3").replace("e", "е")),
    ("zero-width-in-fabricated-number", HEIGHT.replace("330", f"9{ZW}00")),
    ("soft-hyphen-number", HEIGHT.replace("330", "3­30­0")),
]


@pytest.mark.parametrize(("name", "quote"), LOOKALIKES, ids=[n for n, _ in LOOKALIKES])
def test_lookalike_quote_does_not_verify(name: str, quote: str) -> None:
    assert quote != PARIS and quote != HEIGHT
    assert not verify_quote(quote, SOURCE_TEXT)


def test_latin_quote_does_not_verify_against_lookalike_source() -> None:
    spoofed = SOURCE_TEXT.replace("Paris", "Pаris")
    assert not verify_quote(PARIS, spoofed)


def test_same_text_after_normalisation_still_verifies() -> None:
    """Zero-width characters inside an otherwise identical quote, or full-width digits,
    are the same text after normalisation: accepted, as before."""
    assert verify_quote(ZW.join(HEIGHT[:30]), SOURCE_TEXT)
    assert verify_quote(HEIGHT.replace("330", "３３０"), SOURCE_TEXT)


def lookalike_judge(quote: str) -> Any:
    def judge(claim: str, sources: dict[str, str]) -> dict[str, Any]:
        return {
            "verdict": "SUPPORTED",
            "confidence": 1.0,
            "quotes": [{"source_id": "s1", "quote": quote}],
        }

    return judge


@pytest.mark.parametrize(("name", "quote"), LOOKALIKES, ids=[n for n, _ in LOOKALIKES])
async def test_lookalike_evidence_end_to_end(tmp_path: Path, name: str, quote: str) -> None:
    out = await run_guard(
        tmp_path,
        World(judge=lookalike_judge(quote)),
        answer="The tower is 900 metres tall.",
        sources=[Source(id="s1", text=SOURCE_TEXT)],
        cfg=config("fast"),
    )
    report = out.report
    assert report is not None, repr(out.error)
    (result,) = report.claims
    assert result.status != "SUPPORTED"
    assert all(not e.verified for v in result.verdicts for e in v.evidence if e.method == "quote")
