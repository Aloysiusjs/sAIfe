import { FlaskConical, Info } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { Section } from '../Section'
import { presetLabel } from './format'
import { ResidualChart } from './ResidualChart'
import { SAMPLE_RESULTS } from './sample'
import { select } from './select'
import { AblationTable, DetectionTable, TrapTable, VerifierTable } from './Tables'
import type { ResultSummary } from './types'

type Load =
  | { kind: 'loading' }
  | { kind: 'ready'; results: ResultSummary[] }
  | { kind: 'error'; message: string }
  | { kind: 'offline' }

const COMMANDS = [
  'uv run python -m saife bench --set seeded --preset balanced --max-cost 5',
  'uv run python -m saife bench --set seeded --preset balanced --ablate consistency',
  'uv run python -m saife bench --set seeded --preset balanced --verifier same',
  'uv run python -m saife bench --set traps --preset balanced',
]

function HowToProduce({ reason }: { reason: string }) {
  return (
    <Section id="results-empty" title="No benchmark results yet">
      <p className="max-w-[65ch] text-sm leading-relaxed text-ink-700">
        {reason} Results appear here once a benchmark has been run against the live models and its summary saved in{' '}
        <code className="rounded bg-ink-50 px-1 font-mono text-xs">bench/results/</code>. Nothing on this page is
        estimated or filled in: every number comes from a results file. From the repository root:
      </p>
      <pre className="mt-3 overflow-x-auto rounded-md bg-ink-900 px-3 py-2.5 font-mono text-xs leading-relaxed text-ink-50">
        {COMMANDS.join('\n')}
      </pre>
      <p className="mt-3 max-w-[65ch] text-xs leading-relaxed text-ink-600">
        Live runs spend API credit. The runner prints a cost estimate after the first five items and stops if it would
        exceed <code className="font-mono">--max-cost</code>. Interrupted runs resume where they stopped. It exits with an
        error if any claim was shown as supported without verified evidence (a silent failure).
      </p>
    </Section>
  )
}

function Results({ results, example }: { results: ResultSummary[]; example: boolean }) {
  const sel = useMemo(() => select(results), [results])
  const tag = example ? 'Example data, not real results' : undefined
  return (
    <div className="space-y-6">
      {sel.full.length ? (
        <>
          <Section id="results-residual" title="Residual hallucination rate" aside={tag ?? 'Seeded-error set · lower is better'}>
            <ResidualChart runs={sel.full} />
          </Section>
          <Section id="results-detection" title="Detection by preset" aside={tag ?? 'Claim-level, seeded-error set'}>
            <DetectionTable runs={sel.full} />
            <p className="mt-3 max-w-[70ch] text-xs leading-relaxed text-ink-600">
              A claim counts as flagged when it is contradicted or unsupported. False alarms are clean answers shown with
              Caution or Blocked. Errors caught counts injected errors overlapped by a flagged claim.
            </p>
          </Section>
        </>
      ) : null}
      {sel.ablations.map((g) => (
        <Section
          key={g.preset}
          id={`results-ablation-${g.preset}`}
          title={`Detector ablation · ${presetLabel(g.preset)}`}
          aside={tag ?? 'Each row removes one detector; change is against the full preset'}
        >
          <AblationTable group={g} />
        </Section>
      ))}
      {sel.verifier.length ? (
        <Section
          id="results-verifier"
          title="Verifier diversity: cross-provider vs same model"
          aside={tag ?? 'Does an independent verifier catch what self-checking misses?'}
        >
          <VerifierTable pairs={sel.verifier} />
        </Section>
      ) : null}
      {sel.traps.length ? (
        <Section id="results-traps" title="Trap prompts" aside={tag ?? 'Share of runs with at least one flagged claim'}>
          <TrapTable runs={sel.traps} />
        </Section>
      ) : null}
      {!sel.full.length && !sel.traps.length ? (
        <p className="text-sm text-ink-700">
          {sel.total} results file{sel.total === 1 ? '' : 's'} found, but none for a full preset. Run a benchmark without{' '}
          <code className="font-mono text-xs">--ablate</code> to fill in the headline figures.
        </p>
      ) : null}
    </div>
  )
}

export function ResultsPage({ mode }: { mode: 'probing' | 'live' | 'demo' }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [showExample, setShowExample] = useState(false)

  useEffect(() => {
    if (mode === 'probing') return
    if (mode === 'demo') {
      setLoad({ kind: 'offline' })
      return
    }
    let alive = true
    setLoad({ kind: 'loading' })
    api.results().then(
      (results) => alive && setLoad({ kind: 'ready', results }),
      () => alive && setLoad({ kind: 'error', message: 'Could not load results from the sAIfe server.' }),
    )
    return () => {
      alive = false
    }
  }, [mode])

  const real = load.kind === 'ready' && load.results.length > 0
  const reason =
    load.kind === 'offline'
      ? 'Demo mode: the sAIfe server is not connected, so no results files can be read.'
      : load.kind === 'error'
        ? load.message
        : 'The benchmark has not been run yet.'

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <h1 className="text-xl font-semibold text-ink-900">Benchmark results</h1>
      <p className="mt-1 max-w-[70ch] text-sm leading-relaxed text-ink-600">
        sAIfe is scored on in-house passages with labelled, injected errors (numbers, dates, entities, citations,
        negations), plus clean passages that measure false alarms, and on trap prompts built to provoke hallucination.
      </p>

      <div className="mt-6 space-y-6" aria-live="polite">
        {load.kind === 'loading' ? (
          <p className="text-sm text-ink-600">Loading results…</p>
        ) : real ? (
          <Results results={load.results} example={false} />
        ) : (
          <>
            <HowToProduce reason={reason} />
            <div>
              <button
                type="button"
                onClick={() => setShowExample((v) => !v)}
                aria-expanded={showExample}
                className="inline-flex items-center gap-1.5 rounded-md border border-ink-300 bg-white px-3 py-1.5 text-sm font-medium text-ink-800 hover:border-ink-400"
              >
                <FlaskConical aria-hidden="true" className="h-4 w-4" />
                {showExample ? 'Hide the example layout' : 'Show the page layout with example data'}
              </button>
            </div>
            {showExample ? (
              <div className="rounded-lg border-2 border-dashed border-likely-line p-3 sm:p-4">
                <p className="mb-4 flex items-start gap-2 rounded-md bg-likely-bg px-3 py-2 text-sm font-semibold text-likely-fg">
                  <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
                  Example layout, not real results. These numbers are invented to show what the page will contain.
                </p>
                <Results results={SAMPLE_RESULTS} example />
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}
