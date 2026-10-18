from __future__ import annotations

from bench.build_seeded import build
from bench.schema import (
    PASSAGES_PATH,
    SEEDED_PATH,
    TRAPS_PATH,
    Passage,
    SeededItem,
    TrapItem,
    count_token,
    dump_jsonl,
    load_jsonl,
)
from bench.validate import validate_item, validate_seeded, validate_traps
from saife.types import Source


def _passage(i: int) -> Passage:
    text = (
        f"Station {i} was opened in 1912 and is 340 metres long. "
        "It is served by the Northern line. "
        "Its clock tower was designed by Ada Marsh, and the hall is lit by 48 lamps. "
        "Most visitors arrive by train. "
    ) * 3
    source = text.replace("Most visitors arrive by train.", "Visitors mostly come by train.")
    return Passage(
        id=f"t{i:02d}",
        topic="test",
        text=text.strip(),
        sources=[Source(id=f"t{i:02d}-s1", text=source)],
        entity_swaps=[("Ada Marsh", "Hugo Lane"), ("Northern line", "Central line")],
    )


PASSAGES = [_passage(i) for i in range(10)]


def test_build_seeded_is_deterministic() -> None:
    a = dump_jsonl(build(PASSAGES, seed=7))
    b = dump_jsonl(build(PASSAGES, seed=7))
    assert a == b
    assert dump_jsonl(build(PASSAGES, seed=8)) != a


def test_built_set_validates() -> None:
    items = build(PASSAGES, seed=7)
    assert validate_seeded(items, PASSAGES) == []
    assert sum(i.clean for i in items) == 2
    assert all(1 <= len(i.errors) <= 3 for i in items if not i.clean)


def test_validator_catches_bad_labels() -> None:
    items = build(PASSAGES, seed=7)
    by_id = {p.id: p for p in PASSAGES}
    dirty = next(i for i in items if not i.clean)
    clean = next(i for i in items if i.clean)
    passage = by_id[dirty.passage_id]

    shifted = dirty.model_copy(deep=True)
    s, e = shifted.errors[0].span
    shifted.errors[0].span = (s + 1, e + 1)
    assert any("span" in p for p in validate_item(shifted, passage))

    unlabelled = dirty.model_copy(update={"answer": dirty.answer + " Extra."})
    assert any("unlabelled" in p for p in validate_item(unlabelled, passage))

    touched = clean.model_copy(update={"answer": clean.answer.replace("1912", "1913")})
    assert any("byte-identical" in p for p in validate_item(touched, by_id[clean.passage_id]))

    leaked = dirty.model_copy(deep=True)
    injected = leaked.errors[0].injected
    leaked.sources = [Source(id="s", text=f"leak {injected} leak")]
    problems = validate_item(leaked, passage)
    assert any("appears in a source" in p for p in problems)


def test_committed_datasets_are_valid_and_reproducible() -> None:
    passages = load_jsonl(PASSAGES_PATH, Passage)
    items = load_jsonl(SEEDED_PATH, SeededItem)
    traps = load_jsonl(TRAPS_PATH, TrapItem)
    assert len(passages) >= 60
    assert 25 <= len(traps) <= 35
    assert validate_seeded(items, passages) == []
    assert validate_traps(traps) == []
    assert SEEDED_PATH.read_text(encoding="utf-8") == dump_jsonl(build(passages))


def test_count_token_respects_boundaries() -> None:
    assert count_token("in 1969 and 9 more", "9") == 1
    assert count_token("1,000,000 people", "1,000") == 0
    assert count_token("was not here", "was not") == 1
    assert count_token("text (Okafor et al., 2011).", " (Okafor et al., 2011)") == 1
