# sAIfe benchmark datasets

```bash
uv run python -m bench.build_seeded   # regenerate datasets/seeded/seeded.jsonl (deterministic)
uv run python -m bench.validate       # check passages, seeded labels and traps; exit 1 on any problem
```

`bench/metrics.py` computes the scores from `saife.types.Report` objects. `bench/run.py` runs a set through the guard:

```bash
uv run python -m saife bench --set seeded --preset balanced --max-cost 5   # live: spends API credit
uv run python -m saife bench --set seeded --preset balanced --ablate consistency
uv run python -m saife bench --set seeded --preset balanced --verifier same  # every judge on Claude (needs ANTHROPIC_API_KEY and SAIFE_ROUTING=split)
uv run python -m saife bench --set traps --preset strict --limit 10
```

Run it from the repository root. Each item's report is saved to `bench/results/<run_name>/items/<id>.json` as it finishes, so re-running the same command resumes and skips finished items (pass `--date` to resume a run from an earlier day). Items whose run raised are not saved and are retried next time. After the first `min(5, N)` items the runner prints a cost projection, and `--max-cost USD` aborts if the projection is higher. The summary is written to `bench/results/<date>-<set>-<preset>[-ablate-x][-same].json` (`{meta, metrics, items}`) and a `.md` rendered by `report.py`. The silent-failure count is printed in a panel, and the command exits 1 if it is above 0 (exit 2 on a cost abort). Traces go to `runs/bench/` (gitignored). The `/results` page of the web UI reads the summaries through `GET /api/results`.

**TODO:** use the Message Batches API for the bulk resample/judge calls (50% cost). Out of scope for now; every call is synchronous.

## Files

| File | What it is |
| --- | --- |
| `datasets/seeded/passages.jsonl` | 61 clean passages written in-house, each 80–200 words, with the source documents that support them |
| `datasets/seeded/seeded.jsonl` | The seeded-error set, generated from the passages by `build_seeded.py` and committed |
| `datasets/traps/traps.jsonl` | 32 trap prompts that tend to make the subject model hallucinate |
| `schema.py` | Pydantic record types, JSONL load/dump, `count_token` (whole-token matching) |
| `build_seeded.py` | Error injector, seeded with `SEED = 20261009` |
| `validate.py` | Dataset validator |
| `metrics.py` | Pure metric functions plus the `Metrics` model |
| `run.py` | Benchmark runner (`saife bench`): resume, cost guard, ablation, verifier comparison |
| `report.py` | Markdown rendering of a result |
| `results/` | Committed result summaries (empty until a live run) |

## Formats

**Passage**: `{id, topic, text, sources: [{id, text}], entity_swaps: [[original, replacement], ...]}`. Every sentence in `text` is supported by `sources`, the way a RAG ground truth would be. `entity_swaps` lists hand-written wrong-but-plausible substitutes, and none of them appear in the sources.

**Seeded item**: `{id, passage_id, answer, sources, clean, errors: [{type, original, injected, span: [start, end]}]}`
- `type` is one of `number_swap`, `entity_swap`, `date_shift`, `fake_citation` or `negation`.
- `span` is a half-open pair of char offsets into `answer`, so `answer[start:end] == injected`.
- `original` is the clean text that was replaced. It is `""` for a `fake_citation`, which is an insertion. The injected citation includes its leading space.
- Clean items (`clean: true`) have `errors: []` and an `answer` that is byte-identical to the passage.

**Trap**: `{id, category, question, sources?, notes, expected_flag}`. The categories are `fabricated_citation`, `fake_api`, `false_premise`, `obscure_statistic`, `recent_event` and `rag_contradiction`. `fake_api` traps name Python stdlib functions that don't exist (`itertools.chunked`, `math.clamp`, ...), so the deterministic detector can catch them. Every `rag_contradiction` trap ships an invented source that contradicts common belief, and the guard should side with the source.

## How the seeded set is built

A single `random.Random(SEED)` makes every choice, and the passages are processed in file order, so the same input always gives byte-identical output (a unit test checks the committed file against a fresh build).

1. 20% of the passages (12 of 61) are picked to stay clean. They measure the false-alarm rate.
2. Every other passage gets 1–3 non-overlapping edits, with a different error type for each edit:
   - `number_swap`: a non-year number is rescaled (×0.5 to ×2) or nudged (±1–3), keeping its format.
   - `date_shift`: a year (or `AD`/`BC` date) moves by ±1–10 years, never past 2025.
   - `entity_swap`: one of the passage's `entity_swaps`.
   - `fake_citation`: an invented author-year citation such as ` (Okafor et al., 2011)`, inserted before a full stop.
   - `negation`: `is/was/are/were` becomes `... not`.
3. An edit is used only if its injected text appears in neither the clean passage nor any source, and appears exactly once in the final answer, all as whole tokens. That keeps the residual-hallucination check (does the injected text survive?) unambiguous.

The validator checks the following. Every error has a complete label. Each span slices to its injected text. Undoing all the labelled edits gives back the clean passage exactly, so there are no unlabelled changes. Clean items are byte-identical to their passages. Injected text never appears in a source. About 20% of items are clean. Passage word counts and entity swaps are well-formed.

## Metrics (`metrics.py`)

- **Flagged** means a claim status of `CONTRADICTED` or `UNSUPPORTED`. `UNVERIFIABLE` counts as flagged only when you pass `unverifiable_flagged=True`.
- **Claim-level confusion.** A claim is matched to an error when the claim's anchor span in the seeded answer overlaps the error span (half-open). TP is a flagged claim that overlaps an error. FP is a flagged claim that overlaps none. TN is an unflagged claim that overlaps none. FN is an unflagged claim that overlaps an error, plus any error that no claim overlaps at all (a decomposer miss). Claims that can't be located in the seeded answer, such as claims created by repair, are reported as `unmapped_claims` and not scored. Precision, recall and F1 come from these counts, and `errors_detected` gives error-level recall.
- **False-alarm rate**: the share of clean items whose answer status is `CAUTION` or `BLOCKED`.
- **Residual hallucination rate**: the share of injected errors whose injected text still appears in the answer. The baseline is `Report.original_draft` and the guarded figure is `Report.final_answer`.
- **Silent failures**: claims shown as `SUPPORTED` when no verdict has verified evidence, or when any verdict carries an error. The target is 0.
- **Latency and cost**: p50 and p95 of `usage.wall_time_s` (linear interpolation), and the mean of `usage.cost_usd`.
- `flag_rate(reports)` is the trap-set check from Task 4.3: the share of runs with at least one flagged claim. The target is at least 50%.

## Licence and provenance

Every passage, source document and trap prompt was written in-house for sAIfe. Nothing is copied from Wikipedia or any other copyrighted text. The facts are stable, well-known ones (dates, measurements, people), restated in our own words. The `rag_contradiction` sources and some trap subjects (the Model K kettle, the Kestrel language, the Lindqvist Bridge, the Harbourside Rounders League, the "Halvorsen-Okafor effect") are deliberately fictional. The datasets are released under the same licence as this repository.

**Pending:** the licences of the HaluEval QA and RAGTruth subsets still need checking before either is added as `datasets/public/` (Task 5.2). Neither is included yet.
