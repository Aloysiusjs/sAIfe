# sAIfe

A multi-pass hallucination guard for agentic AI, built for the Safety by Design hackathon (AI Safety track).
PRD: `.claude/prds/saife.prd.md` · Plan: `.claude/plans/saife.plan.md` (milestones M0–M6).

## Stack

| Concern | Choice |
| --- | --- |
| Language | Python ≥3.12 (dev on 3.13 via `.python-version`) |
| Packaging | `uv`, hatchling, src layout (`src/saife`) |
| Claude | `anthropic` 1.x (async client; built on `httpx2`) |
| Verifier | `openai` 3.x SDK against any OpenAI-compatible endpoint (`SAIFE_VERIFIER_BASE_URL`; default Google's Gemini endpoint). The same client serves OpenRouter (`OPENROUTER_API_KEY`) |
| Models | `pydantic` v2 |
| Server | FastAPI + SSE (M3) |
| CLI | `typer` + `rich` |
| Tests | `pytest` + `pytest-asyncio`. HTTP mocked with `httpx2.MockTransport`; LLMs faked with `saife.llm.fake.FakeLLM` |
| Lint / types | `ruff`, `mypy --strict` (`native_parser = false`: Windows App Control blocks the compiled parser) |
| Web UI | Vite + React 19 + TS + Tailwind 3 in `web/` (M4) |

No new dependencies without asking.

## Model roles: source of truth is `saife.config.Settings.roles()`

sAIfe runs on the user's own keys from `.env` (template: `.env.example`). Only `SAIFE_VERIFIER_API_KEY` is required.

| Role | Default routing (`SAIFE_ROUTING=verifier`) | `SAIFE_ROUTING=split` (needs `ANTHROPIC_API_KEY`) |
| --- | --- | --- |
| generate | verifier model, unless `SAIFE_ROLE_GENERATE` overrides it (recommended: a different lab's model via OpenRouter) | `claude-haiku-4-5` (accepts `temperature`, sent via `extra_body`) |
| decompose, repair | verifier model, or `SAIFE_ROLE_*` overrides | `claude-opus-5` (adaptive thinking; no `temperature`) |
| web_retrieval | none: web checks fail closed as UNVERIFIABLE | `claude-opus-5` web search |
| grounding / consistency / web judges | verifier model (default `gemini-3.1-flash-lite`) | verifier model; Claude only as a disclosed outage fallback |

`SAIFE_ROLE_<ROLE>=provider:model` overrides any role; provider is `verifier`, `openrouter` or `anthropic`. Under the default routing no Anthropic client is created unless an override names it, and judges never fall back to Claude.

## Safety rules for this codebase (non-negotiable)

These are the product. Each one is enforced by a test, so never weaken a test to make code pass.

1. **Fail closed.** Every LLM failure raises an `LLMError` subclass (`saife/errors.py`). Detectors turn it into `UNVERIFIABLE`. Nothing may treat an error, timeout, refusal or truncation as support.
2. **Evidence is checked by code.** `SUPPORTED`/`CONTRADICTED` need a quote that `evidence.verify_quote` confirms. An LLM never sets `Evidence.verified`.
3. **Fallback only on outages.** The router falls back from verifier to Claude only on `LLMUnavailableError`, only for verifier roles, and only under `FALLBACK_AND_DISCLOSE`. Every fallback is recorded and disclosed.
4. **Bounded loops.** Every loop has a pass, token and time cap, plus a convergence stop.
5. **No silent edits.** Every change from draft to final has a `RepairRecord`.
6. **Untrusted text is data.** Sources, search results and drafts go inside tagged blocks in prompts; outputs are schema-validated.
7. **No secrets anywhere.** Keys come from `.env` only (gitignored). Logs go through `redact.RedactingFilter`; traces store the host and model, never keys or headers.

## Commands

```bash
uv sync
uv run pytest -q -m "not live"          # offline, free, deterministic
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python -m saife config            # show resolved routing (no keys)
uv run python scripts/probe_verifier.py  # live: checks the verifier endpoint, picks the structured-output mode it enforces
```

Live tests are marked `@pytest.mark.live` and spend API credit. Record fixtures for FakeLLM by wrapping a real client in `RecordingLLM`.

## Layout and ownership

```
src/saife/
  config.py      routing, settings, presets (M1)
  errors.py      typed exceptions
  redact.py      secret redaction
  llm/           base protocol · anthropic · openai_compat · router · fake
  pipeline/      generate · decompose · aggregate · repair · loop      (M1–M2)
  detectors/     grounding · consistency · web_evidence · deterministic (M1–M2)
  events.py, notify/, trace.py, server/                                 (M2–M3)
scripts/         one-off operational scripts (probe)
tests/unit/      offline unit tests; tests/invariants/ and tests/redteam/ arrive in M6
bench/           benchmark harness and datasets (M5)
web/             demo UI (M4)
```

Only humans change PRD requirements and this file. When a milestone finishes, update its status in the PRD's Delivery Milestones table and in the plan.
