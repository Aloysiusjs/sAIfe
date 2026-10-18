# sAIfe benchmark: 2026-10-10-seeded-balanced

- Set: `seeded` · preset: `balanced` · ablated: none · verifier: cross
- Detectors: grounding, consistency, deterministic
- Date: 2026-10-10 · items scored: 5 · errored: 0
- Total cost (token-price estimate): $0.42

**Silent failures: 0** (target 0)

## Residual hallucination rate

Share of injected errors whose text is still in the answer.

| Without sAIfe (draft) | With sAIfe (final answer) |
| --- | --- |
| 100.0% | 0.0% |

## Claim-level detection

| Precision | Recall | F1 | False-alarm rate | Errors detected |
| --- | --- | --- | --- | --- |
| 1.000 | 1.000 | 1.000 | n/a | 9 / 9 |

Confusion: TP 9 · FP 0 · TN 66 · FN 0. Unmapped claims (not scored): 9. Items: 5 (0 clean).

## Latency and cost

| p50 latency | p95 latency | Mean cost per item |
| --- | --- | --- |
| 72.4 s | 92.5 s | $0.0839 |

## Models

| Role | Model |
| --- | --- |
| consistency_judge | verifier/gemini-3.1-flash-lite |
| decompose | anthropic/claude-opus-5 |
| generate | anthropic/claude-haiku-4-5 |
| grounding_judge | verifier/gemini-3.1-flash-lite |
| repair | anthropic/claude-opus-5 |
| web_judge | verifier/gemini-3.1-flash-lite |
| web_retrieval | anthropic/claude-opus-5 |
