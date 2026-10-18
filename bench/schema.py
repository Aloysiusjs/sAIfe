"""Dataset record types and JSONL helpers shared by the builder, validator and metrics."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from saife.types import Source

DATASETS = Path(__file__).parent / "datasets"
PASSAGES_PATH = DATASETS / "seeded" / "passages.jsonl"
SEEDED_PATH = DATASETS / "seeded" / "seeded.jsonl"
TRAPS_PATH = DATASETS / "traps" / "traps.jsonl"

ErrorType = Literal["number_swap", "entity_swap", "date_shift", "fake_citation", "negation"]
ERROR_TYPES: tuple[ErrorType, ...] = (
    "number_swap",
    "entity_swap",
    "date_shift",
    "fake_citation",
    "negation",
)

TrapCategory = Literal[
    "fabricated_citation",
    "fake_api",
    "false_premise",
    "obscure_statistic",
    "recent_event",
    "rag_contradiction",
]
TRAP_CATEGORIES: tuple[TrapCategory, ...] = (
    "fabricated_citation",
    "fake_api",
    "false_premise",
    "obscure_statistic",
    "recent_event",
    "rag_contradiction",
)


class Passage(BaseModel):
    """A clean, in-house passage whose every sentence is supported by ``sources``."""

    id: str
    topic: str
    text: str
    sources: list[Source]
    # Hand-written (original, replacement) pairs for entity swaps; replacements are
    # plausible but wrong, and must not occur in the sources.
    entity_swaps: list[tuple[str, str]] = Field(default_factory=list)


class SeededError(BaseModel):
    type: ErrorType
    original: str  # text replaced in the clean passage ("" for an insertion)
    injected: str  # text now in the answer; ``answer[span[0]:span[1]] == injected``
    span: tuple[int, int]


class SeededItem(BaseModel):
    id: str
    passage_id: str
    answer: str
    sources: list[Source]
    clean: bool
    errors: list[SeededError] = Field(default_factory=list)


class TrapItem(BaseModel):
    id: str
    category: TrapCategory
    question: str
    sources: list[Source] | None = None
    notes: str
    expected_flag: str  # what a correct guard should catch


def load_jsonl[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [model.model_validate_json(line) for line in lines if line.strip()]


def dump_jsonl(records: Iterable[BaseModel]) -> str:
    """One compact JSON object per line; ``None`` fields dropped so sources stay ``{id, text}``."""
    return "".join(
        json.dumps(r.model_dump(mode="json", exclude_none=True), ensure_ascii=False) + "\n"
        for r in records
    )


def token_pattern(needle: str) -> re.Pattern[str]:
    # Word-ish boundaries, and digit groups are not split ("1,000" is not inside "1,000,000",
    # "9" is not inside "1969").
    head = r"(?<!\w)(?<!\d[.,])" if needle[:1].isalnum() else ""
    tail = r"(?!\w)(?![.,]\d)" if needle[-1:].isalnum() else ""
    return re.compile(head + re.escape(needle) + tail)


def count_token(text: str, needle: str) -> int:
    """Occurrences of ``needle`` (stripped) in ``text`` as a whole token, case-sensitive."""
    needle = needle.strip()
    if not needle:
        return 0
    return len(token_pattern(needle).findall(text))
