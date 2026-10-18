# PRD — sAIfe: a multi-pass hallucination guard for agentic AI

Safety by Design hackathon · AI Safety track · drafted 4 October 2026

> **sAIfe** is the product name. The Python package and CLI are `saife`.

---

## 0. One-line pitch

An agent that checks its own work before the user sees it: every answer is split into individual claims, each claim is checked by independent detectors over several passes, unsupported claims are repaired or removed, and the user is told plainly which parts were verified, which were changed and which could not be checked.

---

## 1. Problem

LLM answers sound equally confident whether they are right or wrong. The user cannot tell which sentence of a fluent paragraph was made up. Three facts make this worse in agentic systems:

1. **Errors compound.** An agent that hallucinates a fact in step 2 uses it as input to step 3. By the final answer, the bad fact has been restated, built on and is buried.
2. **Self-review is too often the same model with the same blind spots.** When the generator also grades its own work, its errors are correlated with the grader's. "Are you sure?" prompts change the tone more than the accuracy.
3. **When a safety check fails, it fails silently.** If a verifier times out or errors, most pipelines ship the answer anyway, and nothing in the UI shows that the check never ran.

**Who feels it:** anyone acting on LLM output without the time to check every sentence. That includes analysts writing briefs, support agents answering customers, students and developers trusting generated API usage.

**Why it matters for safety:** hallucination is the most common real-world failure of deployed LLMs. The harm comes less from the false statement itself than from the user not knowing it is false.

---

## 2. Thesis (what "Safety by Design" means here)

The safety properties are part of the architecture. They do not depend on a prompt asking the model to be careful.

| # | Principle | How sAIfe enforces it structurally |
|---|---|---|
| P1 | **Fail closed** | A claim is only ever marked `SUPPORTED` with positive, checkable evidence. Detector errors, timeouts, refusals and budget exhaustion all resolve to `UNVERIFIABLE`, never to `SUPPORTED`. |
| P2 | **Evidence must be mechanically checkable** | A verifier that cites a source must return an exact quote. Code, not an LLM, then checks that the quote really appears in the source text. A verifier that invents evidence is caught deterministically. |
| P3 | **Independent verifiers** | Verifiers never see the generator's reasoning or the rest of the draft, only one claim plus the evidence. There are several detector types (grounding, self-consistency, external search, deterministic). **The LLM verifiers run on a different provider's model from the generator**, so the generator and verifier don't share blind spots. |
| P4 | **Bounded loops** | Hard caps on passes, tokens and wall-clock time, plus convergence detection: if a pass doesn't shrink the set of flagged claims, the loop stops. The safety loop can't itself run away. |
| P5 | **No silent edits** | Every repair is recorded as a diff with a reason. The user can always see the original draft and why each change was made. |
| P6 | **Abstention is a valid outcome** | If residual risk stays above threshold, sAIfe blocks or hedges the answer and says why. It does not ship a confident wrong answer. |
| P7 | **Untrusted input is data** | Retrieved documents, web results and the draft itself are wrapped and labelled as data in every verifier prompt. Verifier outputs are schema-constrained, so injected instructions can't change control flow. |
| P8 | **Full audit trail** | Every run writes a JSONL trace with every pass, claim, verdict, piece of evidence, repair, token count and timing, so the run can be replayed. |

---

## 3. Users and core scenarios

| Persona | Scenario | What they need from sAIfe |
|---|---|---|
| **End user** (demo UI) | Asks a question; reads the answer | Inline highlighting per claim, a clear banner ("2 claims could not be verified and were removed"), and evidence one click away |
| **Agent builder** (SDK) | Wraps their existing agent or LLM call | `guard(generate_fn)` or `Guard.check(answer, sources)`, a typed `Report`, and event callbacks or a webhook for alerts |
| **Reviewer / auditor** | Investigates a flagged answer later | A replayable trace, the original-to-final diff, and per-detector verdicts |
| **Hackathon judge** | Wants evidence it works | Live demo plus benchmark numbers: detection precision/recall, residual hallucination rate before vs after, false-alarm rate and cost |

**Input modes**

1. **Generate and guard**: sAIfe calls the subject model itself, then verifies.
2. **Bring your own answer**: paste or POST any answer from any model, optionally with source documents, and sAIfe verifies it. This mode doesn't depend on a provider, which matters for adoption.
3. **Grounded (RAG) mode**: the caller supplies source documents, and grounding is checked against those first.

---

## 4. How it works

```
          ┌──────────── budget / pass / time guards (P4) ─────────────┐
 query ─▶ GENERATE ─▶ DECOMPOSE ─▶ VERIFY (parallel detectors) ─▶ AGGREGATE
                          ▲                                           │
                          │                         all high-risk resolved? ──yes──▶ DELIVER
                          │                                           │ no
                          └──── re-verify changed claims only ◀── REPAIR (only if budget left
                                                                       and still converging)
                                                                       │ else
                                                                       ▼
                                                               DELIVER as CAUTION / BLOCKED
 every transition ─▶ event bus ─▶ UI (SSE) · webhook · console · JSONL trace
```

### 4.1 Pipeline stages

| Stage | Responsibility | Output |
|---|---|---|
| **Generate** | Produce the draft answer (skipped in bring-your-own-answer mode) | `draft: str` |
| **Decompose** | Split the draft into atomic, checkable claims, each with character offsets into the draft and a type (`entity`, `numeric`, `date`, `citation`, `causal`, `code_api`, `opinion`) | `list[Claim]` |
| **Verify** | Run the enabled detectors concurrently over the claims worth checking | `list[DetectorVerdict]` per claim |
| **Aggregate** | Combine the detector verdicts using conservative rules (§4.3) | `ClaimResult` per claim plus the answer-level status |
| **Repair** | Rewrite only the flagged spans: correct them with quoted evidence, hedge them or remove them. It may not add new unsupported claims, and any new claim it does add is re-verified on the next pass | new draft plus `RepairRecord`s |
| **Deliver / Notify** | Emit the final answer, the `Report` and any alerts | `Report`, events |

### 4.2 Detectors (pluggable — one `Detector` protocol)

| Detector | Catches | Mechanism | Evidence check |
|---|---|---|---|
| **Grounding** | Claims not supported by, or contradicted by, the supplied sources | An LLM judge sees one claim plus the source chunks and returns a verdict and exact quotes | The quote must match the source text exactly (after whitespace normalisation). If it doesn't, the verdict is downgraded to `UNVERIFIABLE` (P2) |
| **Self-consistency** | Confabulation: facts the model gives differently on every resample | Resample N answers at varied prompts, then judge whether each claim is supported across the samples (the SelfCheckGPT idea) | Agreement ratio. This is never enough on its own to mark a claim `SUPPORTED` (it can only mark `LIKELY`) |
| **External evidence** | World-knowledge errors when no sources are supplied | Claude web search tool (`web_search_20250305`, needs `ANTHROPIC_API_KEY` and `split` routing): retrieve, then judge with quotes and URLs. Without it, web checks fail closed as `UNVERIFIABLE` | Quote must appear in the returned result content |
| **Deterministic** | Arithmetic, dates, made-up references, fake URLs or DOIs, made-up library APIs | Code: recompute the numbers, parse the dates, resolve URLs/DOIs with a HEAD request, introspect importable Python modules for a named API | Pure code — no LLM in the loop |

### 4.3 Verdicts and aggregation (fail-closed)

Detector verdict: `SUPPORTED | CONTRADICTED | NOT_FOUND | UNVERIFIABLE`, plus confidence and evidence.

Final claim status:

| Status | Rule | Default action |
|---|---|---|
| `CONTRADICTED` | Any detector reports `CONTRADICTED` with verified evidence | Repair. High-severity alert |
| `SUPPORTED` | At least one detector reports `SUPPORTED` with verified evidence, and none contradicts | Keep. Green |
| `LIKELY` | Only consistency-type support, with no verified evidence | Keep. Amber marker |
| `UNSUPPORTED` | Detectors ran and found nothing either way | Repair (hedge or remove) under the `strict` and `balanced` presets |
| `UNVERIFIABLE` | Detectors failed, timed out or refused, or the claim is not factual | Keep, clearly marked. Never shown as verified |

Answer-level outcome: **VERIFIED** (no residual high-risk claims), **CAUTION** (unverified claims remain and are disclosed) or **BLOCKED** (contradicted claims survived the passes, or the residual risk score is above the preset threshold. The answer is withheld or replaced with an abstention and the reason).

### 4.4 Presets

| Preset | Detectors | Max passes | Use |
|---|---|---|---|
| `fast` | grounding (if sources) + deterministic | 1 | Low-latency check, no repair |
| `balanced` (default) | + consistency (N=3) | 2 | General use |
| `strict` | + external evidence, consistency N=5 | 3 | High-stakes. Blocks on any `CONTRADICTED` |

### 4.5 Notification surface

- **Event stream** (typed): `run.started`, `pass.started`, `claim.extracted`, `claim.verdict`, `repair.applied`, `alert.raised{severity, claim_ids, message}`, `run.completed{status}`, `run.failed`.
- **Sinks**: SSE (UI), webhook (POST JSON, HMAC-signed), console/CLI, Python callback.
- **User-facing copy rule**: every alert states *what* was affected, *what sAIfe did* and *what the user should do*, e.g. "1 statistic could not be verified and was removed. Check the original source before citing it."

---

## 5. Scope

### In scope (MVP for the hackathon)

- Python package `saife`: pipeline, the 4 detectors, aggregation, repair loop, budgets, trace, event bus
- CLI: `saife check`, `saife run`, `saife bench`
- FastAPI server: create a run, stream its events over SSE, fetch its report
- React demo UI: question box, live pass timeline, claim highlighting, evidence drawer, draft→final diff, alert banner, preset switch, a bring-your-own-answer tab and a set of prepared "trap" questions
- Benchmark harness and results page (seeded-error set + trap set + one public set if its licence allows)
- Prompt-injection red-team tests for the verifier prompts

- Cross-provider verifier routing: each pipeline role can be assigned to a provider and model

### Stretch

- MCP server exposing a `verify_answer` tool so any MCP-capable agent can call sAIfe
- Per-step guarding of multi-step agent traces (verify intermediate tool outputs, not just the final answer)
- Calibration plot (confidence vs empirical accuracy)

### Out of scope

- Training or fine-tuning models
- Guaranteeing truth. sAIfe reduces and *discloses* hallucination risk; it doesn't certify correctness
- Multi-tenant auth, billing, production deployment
- Non-English content

---

## 6. Requirements

### Functional

| ID | Requirement | Priority |
|---|---|---|
| F1 | Decompose any answer into atomic claims with exact character spans into the draft | Must |
| F2 | Run enabled detectors concurrently; each returns verdict + confidence + evidence | Must |
| F3 | Mechanically verify every quoted piece of evidence against its source; downgrade on mismatch | Must |
| F4 | Aggregate with the fail-closed rules in §4.3 | Must |
| F5 | Repair loop with max passes, token budget, wall-clock timeout and convergence stop | Must |
| F6 | Re-verify only new or changed claims after a repair | Must |
| F7 | Emit typed events; SSE, webhook, console and callback sinks | Must |
| F8 | JSONL trace per run, with a replay command to re-render a report from its trace | Must |
| F9 | Bring-your-own-answer mode (answer + optional sources, no generation) | Must |
| F10 | Demo UI showing the passes live, highlights, evidence, diff and alerts | Must |
| F11 | Benchmark CLI that outputs metrics JSON and a markdown/HTML summary | Must |
| F12 | Presets plus per-run overrides | Should |
| F13 | MCP `verify_answer` tool | Could |

### Non-functional

| ID | Requirement | Target |
|---|---|---|
| N1 | Latency, `balanced` preset, ~200-word answer | p50 ≤ 20 s, with the first claim verdict on screen ≤ 6 s |
| N2 | Cost per `balanced` run | Measured and shown in the UI per run. Target to be set after the first benchmark |
| N3 | Determinism in tests | All unit and integration tests run offline against recorded LLM fixtures |
| N4 | Runs on the user's own keys; no secrets in the repo or traces | Users supply their own keys in `.env` (template `.env.example`, no keys in it). Only `SAIFE_VERIFIER_API_KEY` is required; `OPENROUTER_API_KEY` and `ANTHROPIC_API_KEY` are optional. Keys are read from the environment only. Traces and logs never contain headers or keys. `.env` is gitignored |
| N5 | Accessibility | Status never shown by colour alone (icon + label). WCAG AA contrast |

---

## 7. Success metrics (what we'll show judges)

| Metric | Definition | Target (to tune after the first run) |
|---|---|---|
| Claim-level detection recall | Planted or known-false claims flagged / all false claims | ≥ 0.80 on the seeded set |
| Claim-level precision | True false claims among all flagged / all flagged | ≥ 0.75 |
| False-alarm rate on clean answers | Clean answers that come out BLOCKED or CAUTION | ≤ 15% |
| **Residual hallucination rate** | False claims still in the *final* output: single pass without sAIfe vs with sAIfe | Large relative reduction (headline number) |
| Silent-failure rate | Runs where a detector failed but the claim was still shown as verified | **0** (enforced by tests) |
| Ablation | How each metric changes with each detector turned off | Every detector earns its place |
| **Verifier diversity** | Recall and precision with the verifier on the same model (Claude) vs on the other provider | Shows whether cross-model checking catches errors that same-model checking misses (headline #2) |

---

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Correlated judge errors (the same model is fooled twice) | Missed hallucinations | Cross-provider verifier, deterministic quote check (P2), different detector types, verifier isolation (P3) |
| Second provider lacks strict structured output, or has different limits | Parse failures, 429s | Pydantic validation + one repair retry. A parse failure → `UNVERIFIABLE` (fail closed). A separate concurrency limit for each provider |
| The latest models rarely hallucinate on easy prompts, so the demo looks empty | Weak demo | Prepared trap questions (fabricated citations, false premises, obscure figures), bring-your-own-answer mode, and a seeded-error set with known ground truth |
| Too many false positives, so users ignore the alerts | Alert fatigue | Calibrate thresholds on the seeded set. `LIKELY` tier instead of a binary verdict. Report the false-alarm rate honestly |
| Repair introduces new hallucinations | Worse output | Repair is limited to removing, hedging or replacing with quoted evidence. Changed claims are re-verified. Convergence stop |
| Prompt injection via sources or web results | Verifier manipulated into "SUPPORTED" | Data tagging, schema-constrained outputs, mechanical quote check, red-team test suite |
| Cost and latency of multiple passes | Slow demo | asyncio fan-out, prompt caching of sources, presets, verify only check-worthy claims |
| Web search unavailable or rate-limited | Missing evidence | Fail closed to `UNVERIFIABLE` and say so in the UI |
| Public benchmark licence or format issues | Can't publish numbers | Seeded-error and trap sets are built in-house, so no external dependency; any public set is an extra |

---

## 9. Open decisions

1. ~~Verifier model.~~ **Decided 2026-10-04: cross-provider.** Claude splits claims, runs web search and repairs (`claude-opus-5`) and generates answers (`claude-haiku-4-5`, see #2). The team's second-provider model (key supplied by the team) runs the verifier judgements: grounding, consistency and the web-evidence verdict. Remaining questions are below.
   - 1a. ~~Which provider?~~ **Decided: `gemini-3.1-flash-lite` through an OpenAI-compatible endpoint** (the team used the gateway `https://api.apiyi.com/v1` during development; since decision 5, users point it at their own endpoint, by default Google's). The adapter is a generic OpenAI-compatible client, so the same code can point at Google's own OpenAI-compatible endpoint or any other gateway just by changing `base_url`. What M0 must confirm: that the model ID is listed at `/v1/models`, and whether `response_format: json_schema` is honoured through the gateway.
   - 1c. **Third-party gateway risk (disclose in the pitch).** Verifier prompts (claims + source excerpts) pass through an intermediary that isn't Google or Anthropic. That's fine for public benchmark data and demo questions. For sensitive data, the README will recommend pointing `base_url` at the provider's own endpoint. A *Flash-Lite*-class judge is small: the verifier-diversity benchmark will show honestly whether it is precise enough, and the quote check covers fabricated evidence regardless of judge size.
   - 1b. If the second provider is unavailable, should the run fall back to Claude as verifier (and say so in the report, marked `verifier_diversity: false`), or fail closed to `UNVERIFIABLE`? Proposed: fall back and disclose it in `balanced`; fail closed in `strict`.
2. ~~Subject model.~~ **Decided 2026-10-04: `claude-haiku-4-5` generates answers.** It hallucinates more often, so the demo has real errors to catch, and it is cheap and fast. A stronger model (`claude-opus-5`) does the claim splitting and repair, because those steps need precision. The generator model can be configured, so the benchmark can also report a run with an Opus generator.
3. **Public benchmark.** Candidates are a HaluEval QA subset and a RAGTruth subset. Licences and formats need checking in M5 before we commit.
4. ~~Name.~~ **Decided 2026-10-04: sAIfe.**
5. ~~Whose keys?~~ **Decided 2026-10-18: sAIfe runs on the user's own API keys.** The only required key is for an OpenAI-compatible verifier endpoint; `.env.example` defaults to Google's own Gemini endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`, free key from Google AI Studio) instead of a third-party gateway. Default routing (`SAIFE_ROUTING=verifier`) runs every role on the verifier model and needs no Anthropic key. Any role can be moved with `SAIFE_ROLE_<ROLE>=provider:model` (providers: `verifier`, `openrouter`, `anthropic`). To keep P3 with free keys, route the generator to a different lab's model on OpenRouter (e.g. `nvidia/nemotron-3-super-120b-a12b:free`). The original routing in decisions 1 and 2 remains available as `SAIFE_ROUTING=split`. Trade-off: without an Anthropic key there is no web evidence.

---

## Delivery Milestones

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| M0 | Scaffold | Repo, uv project, lint/type/test CI, FakeLLM fixtures | done | `.claude/plans/saife.plan.md` |
| M1 | Core loop (single pass) | Decompose → grounding + deterministic → aggregate → Report, via the CLI | done (live-checked) | `.claude/plans/saife.plan.md` |
| M2 | Multi-pass and full detectors | Consistency + web evidence, repair loop, budgets, convergence, trace | done (live-checked; web evidence only with an Anthropic key) | `.claude/plans/saife.plan.md` |
| M3 | Server and notifications | FastAPI + SSE + webhook + replay | done (live API + SSE check 2026-10-18; webhook unit-tested) | `.claude/plans/saife.plan.md` |
| M4 | Demo UI | Live timeline, highlights, evidence, diff, alerts, trap set | done (demo mode; live UI run + visual check pending) | `.claude/plans/saife.plan.md` |
| M5 | Benchmark | Seeded + trap (+ public) sets, metrics, ablation, results page | built; 5-item smoke run only; full run pending | `.claude/plans/saife.plan.md` |
| M6 | Hardening and pitch | Red-team suite, docs, demo script, pitch deck | done (red-team, invariants, README, demo script, pitch and technical decks); deck placeholders and final figures pending | `.claude/plans/saife.plan.md` |
