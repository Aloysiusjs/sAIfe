# sAIfe demo script (3 minutes)

Goal: show one hallucination caught live, one repaired from evidence, and the numbers. Every beat has an offline fallback.

## Before you go on stage

```bash
uv run uvicorn saife.server.app:app --port 8000      # terminal 1
cd web && npm run dev                                  # terminal 2 → http://localhost:5173
```

- Open two tabs: `http://localhost:5173` (live) and `http://localhost:5173/?demo=1` (recorded run, no network).
- Keys: sAIfe runs on your own keys in `.env` (see the README's *Keys and routing*). Run `uv run python -m saife config` and check the routing you plan to show; with the recommended free setup the generator is on OpenRouter and the judges on Gemini.
- Warm up once: run beat 2 a few minutes before, so the endpoints and the API are both awake. Free OpenRouter models allow 50 requests a day: don't spend them rehearsing beat 1 over and over.
- Paste the beat 2 answer and source into a text file you can copy from.
- Fallback rule: if a live run has not shown its first verdict within 15 s, switch to the `?demo=1` tab and keep talking. The recorded run walks through the same story.

## 0:00 – 0:25 · The problem (no screen yet, or the cover slide)

> "Language models sound exactly as sure when they're wrong. In an agent, a made-up fact in step two becomes the input to step three. And when a safety check fails, most systems ship the answer anyway. sAIfe checks every claim before anyone reads it, and when it can't check something, it says so."

## 0:25 – 1:10 · Beat 1: a made-up function, caught by code

1. **Trap questions** tab → pick *"How does math.clamp(x, lo, hi) handle NaN inputs?"* (`trap-fake-api-05`). Preset: **balanced**. Run.
2. While the timeline fills: "The answer is split into individual claims. Each one goes to independent detectors in parallel."
3. When the `math.clamp` claim turns **Contradicted**: click it to open the evidence drawer.

> "No language model made this call. The deterministic detector imported Python's math module and looked: `clamp` isn't there. That's evidence code can confirm."

Note: whether the subject model invents `math.clamp` varies run to run. If it correctly says the function doesn't exist, say so — "the model got it right this time, and every claim it made is checked" — and move on.

## 1:10 – 2:10 · Beat 2: a wrong date, corrected from the source

1. **Bring your own answer** tab. Paste the answer from `examples/answer_with_errors.md`:
   *"The Eiffel Tower stands on the Champ de Mars in Paris. It was completed in 1901 for the World's Fair. The tower is 330 metres tall, including its antennas. It is painted gold every year by a team of 900 painters."*
2. Paste `examples/sources/eiffel.md` as the source. Preset: **balanced**. Run.
3. Point at the banner and the outcomes:
   - "completed in 1901": **Contradicted**, then corrected to 1889. Open the drawer: the judge's quote, with a **verified** mark that code set, not the model.
   - Gold paint and 900 painters: **Unsupported**, then removed or reworded as "reportedly" (it varies by run).
   - Everything else: **Supported**, each with a verified quote.
   - Banner: **Verified** when every remaining sentence is supported, with alerts listing what was corrected and removed; **Caution** when a hedge remains. Either way, say what changed.
4. Scroll to **Changes from the original draft**.

> "Nothing is edited silently. Every change has a record with the reason and the evidence. The original draft is always one click away."

Measured on live runs: 2026-10-18, default routing (Gemini via the verifier endpoint): 2 passes, about 11 s, 1901 corrected to 1889 and the gold-paint sentence removed, banner Verified. 2026-10-10, Claude + Gemini routing: about 35 s, about $0.08.

## 2:10 – 2:45 · Beat 3: the numbers

Click **Benchmark** in the header (`/results`).

> "We planted errors with known positions into passages that have sources, so we have ground truth. Without sAIfe, [__]% of the planted errors reach the reader. With sAIfe, [__]%. Silent failures — a check failing but the claim still shown as verified — are zero, and a test enforces that."

Fill the blanks from the full seeded run before the demo (the 5-item preliminary run gave 100% → 0%; say "preliminary" if that is all you have).

## 2:45 – 3:00 · Close

> "sAIfe is a Python SDK, a CLI and a REST API with live events. It works on any model's answer. It reduces hallucination risk and, just as important, it tells you what it couldn't check."

## Likely questions

| Question | Short answer |
|---|---|
| Isn't this just asking the model "are you sure"? | No. Verifiers see one claim at a time, run on a different lab's model from the generator (when a second provider is configured), and their quotes are checked by code. |
| What if the verifier is down? | By default the affected claims fail closed as Unverifiable, and the report says so. With the Claude routing (`split`), balanced falls back to Claude and discloses it; strict always fails closed. |
| Prompt injection in the sources? | Sources are escaped data blocks; quotes inside instruction-like text are rejected; there's a red-team test suite. |
| Latency? | 9–16 s for checks on the default routing; 45 s for generate-and-check with a free OpenRouter generator; up to 70 s with Claude routing. Verdicts stream live so the user sees progress. |
| Does it guarantee truth? | No. A verified quote proves the source says it, not that the source is right. |
| Whose keys does it use? | Yours. sAIfe reads keys only from your `.env`; one verifier key (a free Google AI Studio key works) is enough to start. |
