# Example answers for CLI smoke runs

Each answer can be checked against the sources in `sources/`. These runs call live models
on your own keys from `.env` (at minimum `SAIFE_VERIFIER_API_KEY`; see `.env.example`). On Windows,
use `uv run python -m saife` instead of `uv run saife`:

```bash
uv run saife check --answer examples/answer_grounded.md    --sources examples/sources --preset fast
uv run saife check --answer examples/answer_with_errors.md --sources examples/sources --preset balanced
uv run saife check --answer examples/answer_python.md      --sources examples/sources/python.md --preset strict
uv run saife run "When was the Eiffel Tower built?" --sources examples/sources --preset balanced
uv run saife replay runs/<run_id>.jsonl
```

| Answer | What to expect |
| --- | --- |
| `answer_grounded.md` | Every claim is supported by `eiffel.md`, so the answer is VERIFIED |
| `answer_with_errors.md` | "completed in 1901" contradicts the source; the gold-paint claim has no support. Expect a repair, or BLOCKED |
| `answer_python.md` | The arithmetic is recomputed. `json.parse_fast` does not exist, so the deterministic detector catches it |
