"""Dataset validator (plan Task 5.1): ground-truth labels must be exact, or metrics lie.

uv run python -m bench.validate      # exit 1 and list problems if anything is off
"""

from __future__ import annotations

import sys

from bench.schema import (
    ERROR_TYPES,
    PASSAGES_PATH,
    SEEDED_PATH,
    TRAP_CATEGORIES,
    TRAPS_PATH,
    Passage,
    SeededItem,
    TrapItem,
    count_token,
    load_jsonl,
)

MIN_WORDS, MAX_WORDS = 80, 200
MIN_CLEAN_SHARE, MAX_CLEAN_SHARE = 0.15, 0.25


def validate_passages(passages: list[Passage]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for p in passages:
        if p.id in seen:
            problems.append(f"{p.id}: duplicate passage id")
        seen.add(p.id)
        words = len(p.text.split())
        if not MIN_WORDS <= words <= MAX_WORDS:
            problems.append(f"{p.id}: {words} words, want {MIN_WORDS}-{MAX_WORDS}")
        if not p.sources or any(not s.text.strip() for s in p.sources):
            problems.append(f"{p.id}: needs at least one non-empty source")
        for original, replacement in p.entity_swaps:
            if count_token(p.text, original) == 0:
                problems.append(f"{p.id}: entity swap original {original!r} not in text")
            if any(count_token(s.text, replacement) for s in p.sources):
                problems.append(f"{p.id}: entity swap replacement {replacement!r} is in a source")
    return problems


def _restore(item: SeededItem) -> str:
    """Undo every labelled edit; must give back the clean passage exactly."""
    text = item.answer
    for e in sorted(item.errors, key=lambda e: e.span[0], reverse=True):
        start, end = e.span
        text = text[:start] + e.original + text[end:]
    return text


def validate_item(item: SeededItem, passage: Passage) -> list[str]:
    problems: list[str] = []
    tag = item.id
    if item.sources != passage.sources:
        problems.append(f"{tag}: sources differ from passage {passage.id}")
    if item.clean:
        if item.errors:
            problems.append(f"{tag}: clean item has errors")
        if item.answer != passage.text:
            problems.append(f"{tag}: clean item is not byte-identical to its passage")
        return problems
    if not 1 <= len(item.errors) <= 3:
        problems.append(f"{tag}: {len(item.errors)} errors, want 1-3")
    spans = sorted(e.span for e in item.errors)
    for (_, a_end), (b_start, _) in zip(spans, spans[1:], strict=False):
        if b_start < a_end:
            problems.append(f"{tag}: overlapping error spans")
    for e in item.errors:
        label = f"{tag} [{e.type} {e.span}]"
        if e.type not in ERROR_TYPES or not e.injected.strip() or e.injected == e.original:
            problems.append(f"{label}: incomplete label")
        start, end = e.span
        if not 0 <= start < end <= len(item.answer) or item.answer[start:end] != e.injected:
            problems.append(f"{label}: span does not slice to the injected text")
        if count_token(item.answer, e.injected) != 1:
            problems.append(f"{label}: injected text is not unique in the answer")
        if any(count_token(s.text, e.injected) for s in item.sources):
            problems.append(f"{label}: injected text appears in a source")
    if not problems and _restore(item) != passage.text:
        problems.append(f"{tag}: unlabelled change (undoing the labelled edits != passage)")
    return problems


def validate_seeded(items: list[SeededItem], passages: list[Passage]) -> list[str]:
    by_id = {p.id: p for p in passages}
    problems = validate_passages(passages)
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            problems.append(f"{item.id}: duplicate item id")
        seen.add(item.id)
        passage = by_id.get(item.passage_id)
        if passage is None:
            problems.append(f"{item.id}: unknown passage {item.passage_id}")
            continue
        problems.extend(validate_item(item, passage))
    if {i.passage_id for i in items} != set(by_id):
        problems.append("seeded set does not cover every passage exactly")
    if items:
        share = sum(i.clean for i in items) / len(items)
        if not MIN_CLEAN_SHARE <= share <= MAX_CLEAN_SHARE:
            problems.append(f"clean share {share:.2f}, want about 0.20")
    return problems


def validate_traps(traps: list[TrapItem]) -> list[str]:
    problems: list[str] = []
    ids = [t.id for t in traps]
    if len(ids) != len(set(ids)):
        problems.append("duplicate trap ids")
    for t in traps:
        if t.category == "rag_contradiction" and not t.sources:
            problems.append(f"{t.id}: rag_contradiction trap needs sources")
    missing = set(TRAP_CATEGORIES) - {t.category for t in traps}
    if missing:
        problems.append(f"trap categories with no items: {sorted(missing)}")
    return problems


def main() -> int:
    passages = load_jsonl(PASSAGES_PATH, Passage)
    items = load_jsonl(SEEDED_PATH, SeededItem)
    traps = load_jsonl(TRAPS_PATH, TrapItem)
    problems = validate_seeded(items, passages) + validate_traps(traps)
    for line in problems:
        print(line)
    n_err = sum(len(i.errors) for i in items)
    print(
        f"{len(passages)} passages, {len(items)} seeded items ({n_err} errors), "
        f"{len(traps)} traps: {len(problems)} problem(s)"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
