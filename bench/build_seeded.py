"""Build the seeded-error set: ground truth by construction (plan Task 5.1).

Deterministic: one ``random.Random(seed)`` drives every choice, passages are processed in
file order, so the same passages and seed always give byte-identical output.

    uv run python -m bench.build_seeded            # rewrites bench/datasets/seeded/seeded.jsonl
"""

from __future__ import annotations

import argparse
import random
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from bench.schema import (
    ERROR_TYPES,
    PASSAGES_PATH,
    SEEDED_PATH,
    ErrorType,
    Passage,
    SeededError,
    SeededItem,
    count_token,
    dump_jsonl,
    load_jsonl,
    token_pattern,
)

SEED = 20261009
CLEAN_SHARE = 0.2
MAX_ATTEMPTS = 50
MAX_YEAR = 2025

_NUMBER = re.compile(
    r"(?<![\w.,\-])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w,]|\.\d)"
    r"|(?<![\w.,\-])\d+(?:\.\d+)?(?!\w|[.,]\d)"
)
_NEGATABLE = re.compile(r"\b(is|was|are|were)\b(?! not\b| never\b)")
_SENTENCE_END = re.compile(r"(?<=\w)\.(?=\s|$)")

_SURNAMES = (
    "Halvorsen",
    "Okafor",
    "Lindqvist",
    "Moreau",
    "Takahashi",
    "Brennan",
    "Castellanos",
    "Varga",
    "Ndiaye",
    "Whitcombe",
    "Petrakis",
    "Sørensen",
    "Ibarra",
    "Kowalczyk",
    "Achterberg",
)


@dataclass(frozen=True)
class _Edit:
    type: ErrorType
    start: int  # span in the clean text being replaced
    end: int
    original: str
    injected: str


def _is_year(text: str, start: int, end: int) -> bool:
    tok = text[start:end]
    if tok.isdigit() and len(tok) == 4 and 1000 <= int(tok) <= 2099:
        return True
    return text[max(0, start - 3) : start] == "AD " or text[end : end + 3] == " BC"


def _blocked(injected: str, p: Passage) -> bool:
    """An injected string must be new: absent from the clean text and from every source."""
    return count_token(p.text, injected) > 0 or any(
        count_token(s.text, injected) > 0 for s in p.sources
    )


def _swap_number(tok: str, rng: random.Random) -> str:
    raw = tok.replace(",", "")
    if "." in raw:
        decimals = len(raw.split(".")[1])
        step = Decimal(1).scaleb(-decimals)
        value = Decimal(raw)
        new = value + rng.choice([2, 3, 4, 5, 7]) * step * rng.choice([1, -1])
        if new <= 0:
            new = value + 3 * step
        return f"{new:.{decimals}f}"
    n = int(raw)
    if n < 20:
        new_int = n + rng.choice([1, 2, 3, -1, -2])
        if new_int <= 0 or new_int == n:
            new_int = n + 2
    else:
        sig = max(1, len(raw.rstrip("0")))
        new_int = int(round(n * rng.choice([0.5, 0.75, 1.25, 1.5, 2.0]), -(len(raw) - sig)))
        if new_int == n:
            new_int = n + 10 ** (len(raw) - sig)
    return f"{new_int:,}" if "," in tok else str(new_int)


def _shift_year(tok: str, rng: random.Random) -> str:
    year = int(tok)
    for _ in range(10):
        new = year + rng.choice([-10, -5, -3, -2, -1, 1, 2, 3, 5, 10])
        if 0 < new <= MAX_YEAR:
            return str(new)
    return str(year - 1)


def _candidates(p: Passage, rng: random.Random) -> dict[ErrorType, list[_Edit]]:
    text = p.text
    out: dict[ErrorType, list[_Edit]] = {t: [] for t in ERROR_TYPES}
    for m in _NUMBER.finditer(text):
        tok = m.group()
        if _is_year(text, m.start(), m.end()):
            kind: ErrorType = "date_shift"
            new = _shift_year(tok, rng)
        else:
            kind = "number_swap"
            new = _swap_number(tok, rng)
        if new != tok and not _blocked(new, p):
            out[kind].append(_Edit(kind, m.start(), m.end(), tok, new))
    for original, replacement in p.entity_swaps:
        hit = token_pattern(original).search(text)
        if hit and not _blocked(replacement, p):
            out["entity_swap"].append(
                _Edit("entity_swap", hit.start(), hit.end(), original, replacement)
            )
    for m in _SENTENCE_END.finditer(text):
        names = rng.sample(_SURNAMES, 2)
        cite = (
            f" ({names[0]} et al., {rng.randint(1985, 2022)})"
            if rng.random() < 0.5
            else f" ({names[0]} and {names[1]}, {rng.randint(1985, 2022)})"
        )
        if not _blocked(cite, p):
            out["fake_citation"].append(_Edit("fake_citation", m.start(), m.start(), "", cite))
    for m in _NEGATABLE.finditer(text):
        verb = m.group(1)
        new = f"{verb} not"
        if not _blocked(new, p):
            out["negation"].append(_Edit("negation", m.start(), m.end(), verb, new))
    return out


def _apply(text: str, edits: list[_Edit]) -> tuple[str, list[SeededError]]:
    parts: list[str] = []
    errors: list[SeededError] = []
    pos = 0
    out_len = 0
    for e in sorted(edits, key=lambda e: e.start):
        chunk = text[pos : e.start]
        parts.append(chunk)
        out_len += len(chunk)
        errors.append(
            SeededError(
                type=e.type,
                original=e.original,
                injected=e.injected,
                span=(out_len, out_len + len(e.injected)),
            )
        )
        parts.append(e.injected)
        out_len += len(e.injected)
        pos = e.end
    parts.append(text[pos:])
    return "".join(parts), errors


def _overlaps(a: _Edit, b: _Edit) -> bool:
    # Keep a 2-char gap so adjacent edits never merge into one token.
    return a.start < b.end + 2 and b.start < a.end + 2


def _inject(p: Passage, rng: random.Random) -> tuple[str, list[SeededError]]:
    for _ in range(MAX_ATTEMPTS):
        cands = _candidates(p, rng)
        types = [t for t in ERROR_TYPES if cands[t]]
        rng.shuffle(types)
        k = rng.choice([1, 2, 3])
        chosen: list[_Edit] = []
        for t in types:
            if len(chosen) == k:
                break
            options = list(cands[t])
            rng.shuffle(options)
            for e in options:
                if not any(_overlaps(e, c) for c in chosen):
                    chosen.append(e)
                    break
        if not chosen:
            continue
        answer, errors = _apply(p.text, chosen)
        if all(count_token(answer, e.injected) == 1 for e in errors):
            return answer, errors
    raise ValueError(f"could not inject errors into passage {p.id}")


def build(passages: list[Passage], *, seed: int = SEED) -> list[SeededItem]:
    rng = random.Random(seed)  # noqa: S311 -- seeded, deterministic, not for crypto
    n_clean = round(len(passages) * CLEAN_SHARE)
    clean_ids = {p.id for p in rng.sample(passages, n_clean)}
    items: list[SeededItem] = []
    for p in passages:
        errors: list[SeededError] = []
        if p.id in clean_ids:
            answer = p.text
        else:
            answer, errors = _inject(p, rng)
        items.append(
            SeededItem(
                id=f"seeded-{p.id}",
                passage_id=p.id,
                answer=answer,
                sources=p.sources,
                clean=p.id in clean_ids,
                errors=errors,
            )
        )
    return items


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--passages", type=Path, default=PASSAGES_PATH)
    parser.add_argument("--out", type=Path, default=SEEDED_PATH)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    items = build(load_jsonl(args.passages, Passage), seed=args.seed)
    args.out.write_text(dump_jsonl(items), encoding="utf-8", newline="\n")
    n_err = sum(len(i.errors) for i in items)
    print(f"wrote {len(items)} items ({sum(i.clean for i in items)} clean, {n_err} errors)")


if __name__ == "__main__":
    main()
