import { ChevronRight, FlaskConical, OctagonX, Target, CircleCheck } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { getBench } from '../api'
import type { BenchSummary } from '../../api/_lib/core'
import { usePolling } from '../lib/usePolling'
import { fmtDateTime, fmtDuration, fmtInt, fmtPct, fmtUsd } from '../lib/format'
import type { Connection } from './Header'
import { Freshness } from './Freshness'
import { EmptyState, Notice, Panel, Spinner } from './Panel'

interface Props {
  password: string
  onApiError: (e: unknown) => boolean
  onConnection: (c: Connection) => void
}

const TRAP_TARGET = 0.5

function metric(b: BenchSummary, key: string): number | null {
  const v = (b.metrics as Record<string, unknown> | undefined)?.[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function Stat({ label, value, hint, children }: { label: string; value?: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="label-xs" title={hint}>
        {label}
      </dt>
      <dd className="mt-0.5 text-base font-semibold tabular-nums">{children ?? value}</dd>
    </div>
  )
}

function SilentFailures({ n }: { n: number | null }) {
  if (n === null) return <span className="text-ink-400">–</span>
  if (n > 0)
    return (
      <span className="inline-flex items-center gap-1 rounded bg-contradicted-bg px-1.5 py-0.5 text-sm text-contradicted-fg dark:bg-contradicted-line/25 dark:text-contradicted-bg">
        <OctagonX aria-hidden="true" className="h-3.5 w-3.5" />
        {fmtInt(n)} silent
      </span>
    )
  return (
    <span className="inline-flex items-center gap-1 text-supported-fg dark:text-supported-bg">
      <CircleCheck aria-hidden="true" className="h-3.5 w-3.5" />0
    </span>
  )
}

function SeededStats({ b }: { b: BenchSummary }) {
  const before = metric(b, 'residual_baseline')
  const after = metric(b, 'residual_guarded')
  return (
    <>
      <Stat label="Precision" value={fmtPct(metric(b, 'precision'))} hint="Share of flagged claims that were real errors" />
      <Stat label="Recall" value={fmtPct(metric(b, 'recall'))} hint="Share of seeded errors that were flagged" />
      <Stat label="F1" value={fmtPct(metric(b, 'f1'))} />
      <Stat label="False alarms" value={fmtPct(metric(b, 'false_alarm_rate'), 'n/a')} hint="Clean items wrongly flagged" />
      <Stat label="Residual hallucination" hint="Share of items still wrong: without sAIfe → with sAIfe">
        <span className="whitespace-nowrap">
          {fmtPct(before)} <span className="font-normal text-ink-400">→</span> {fmtPct(after)}
        </span>
      </Stat>
    </>
  )
}

function TrapStats({ b }: { b: BenchSummary }) {
  const rate = metric(b, 'flag_rate')
  const met = rate !== null && rate >= TRAP_TARGET
  return (
    <Stat label="Trap flag rate" hint="Share of trap prompts flagged; target at least 50%">
      <span className="inline-flex flex-wrap items-center gap-2">
        {fmtPct(rate)}
        {rate !== null ? (
          <span
            className={`inline-flex items-center gap-1 text-xs font-medium ${met ? 'text-supported-fg dark:text-supported-bg' : 'text-unsupported-fg dark:text-unsupported-bg'}`}
          >
            <Target aria-hidden="true" className="h-3.5 w-3.5" />
            {met ? 'meets' : 'below'} ≥{fmtPct(TRAP_TARGET)} target
          </span>
        ) : null}
      </span>
    </Stat>
  )
}

function BenchCard({ b }: { b: BenchSummary }) {
  const isTraps = b.set === 'traps'
  const items = metric(b, 'items') ?? b.n_items
  const confusion = (b.metrics as Record<string, unknown> | undefined)?.confusion
  const cm = typeof confusion === 'object' && confusion !== null ? (confusion as Record<string, unknown>) : null
  const cmv = (k: string) => (cm && typeof cm[k] === 'number' ? fmtInt(cm[k] as number) : '–')
  const models = Object.entries(b.models ?? {})
  const errDet = metric(b, 'errors_detected')
  const errTot = metric(b, 'errors_total')
  return (
    <li className="panel p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="min-w-0 break-all font-mono text-sm font-semibold">{b.name}</h2>
        <span className="text-xs text-ink-500 dark:text-ink-400">{b.date || fmtDateTime(b.received_at)}</span>
      </div>
      <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-600 dark:text-ink-300">
        <span className="rounded bg-ink-100 px-1.5 py-0.5 font-semibold uppercase tracking-wide dark:bg-ink-800">{b.set || 'unknown set'}</span>
        {b.preset ? <span>preset {b.preset}</span> : null}
        {b.verifier ? <span>verifier <span className="font-mono">{b.verifier}</span></span> : null}
        <span>{fmtInt(items)} items</span>
        {b.n_errors ? <span className="text-contradicted-fg dark:text-contradicted-bg">{fmtInt(b.n_errors)} item errors</span> : null}
        {b.ablate?.length ? <span>ablated: {b.ablate.join(', ')}</span> : null}
      </p>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
        {isTraps ? <TrapStats b={b} /> : <SeededStats b={b} />}
        <Stat label="Silent failures" hint="Errors that passed as verified">
          <SilentFailures n={metric(b, 'silent_failures')} />
        </Stat>
        <Stat label="Latency p50" value={fmtDuration(metric(b, 'latency_p50_s'))} />
        <Stat label="Latency p95" value={fmtDuration(metric(b, 'latency_p95_s'))} />
        <Stat label="Cost / item" value={fmtUsd(metric(b, 'mean_cost_usd'))} />
        <Stat label="Total cost" value={fmtUsd(b.total_cost_usd)} />
      </dl>
      <details className="group mt-3 border-t border-ink-100 pt-2 dark:border-ink-800">
        <summary className="flex cursor-pointer list-none items-center gap-1 rounded text-xs font-medium text-ink-600 dark:text-ink-300">
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
          Models and details
        </summary>
        <div className="mt-2 grid gap-3 text-xs sm:grid-cols-2">
          <dl className="space-y-1">
            {models.length ? (
              models.map(([role, model]) => (
                <div key={role} className="flex min-w-0 gap-2">
                  <dt className="w-28 shrink-0 text-ink-500 dark:text-ink-400">{role}</dt>
                  <dd className="min-w-0 break-all font-mono">{model}</dd>
                </div>
              ))
            ) : (
              <p className="text-ink-500 dark:text-ink-400">No model roles recorded.</p>
            )}
          </dl>
          <div className="space-y-1 text-ink-600 dark:text-ink-300">
            {b.detectors?.length ? <p>Detectors: {b.detectors.join(', ')}</p> : null}
            {errTot !== null ? (
              <p>
                Errors detected: {fmtInt(errDet)} of {fmtInt(errTot)}
              </p>
            ) : null}
            {metric(b, 'clean_items') !== null ? <p>Clean items: {fmtInt(metric(b, 'clean_items'))}</p> : null}
            {cm ? (
              <p className="tabular-nums">
                Confusion: TP {cmv('tp')} · FP {cmv('fp')} · TN {cmv('tn')} · FN {cmv('fn')}
              </p>
            ) : null}
            <p>Received {fmtDateTime(b.received_at)}</p>
          </div>
        </div>
      </details>
    </li>
  )
}

export function Bench({ password, onApiError, onConnection }: Props) {
  const polled = usePolling('bench', (signal) => getBench(password, signal), null)
  const { data, error } = polled
  useEffect(() => {
    if (error && onApiError(error)) return
    onConnection(error ? (data ? 'stale' : 'offline') : data ? 'ok' : 'unknown')
  }, [error, data, onApiError, onConnection])

  const list = [...(data?.bench ?? [])].sort((a, b) => (b.received_at ?? 0) - (a.received_at ?? 0))
  return (
    <main className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Benchmarks</h1>
        <Freshness updatedAt={polled.updatedAt} loading={polled.loading} stale={!!error && !!data} onRefresh={polled.refresh} />
      </div>
      {!data ? (
        error ? (
          <Notice tone="error">Could not load benchmarks: {error instanceof Error ? error.message : 'unknown error'}</Notice>
        ) : (
          <Spinner label="Loading benchmarks" />
        )
      ) : list.length === 0 ? (
        <Panel>
          <EmptyState icon={<FlaskConical aria-hidden="true" className="h-7 w-7" />} title="No benchmark results yet">
            <p>
              Publish a saved result with <code className="code-chip">uv run python -m saife publish-bench bench/results/&lt;name&gt;.json</code>.
              When the dashboard is configured in sAIfe&apos;s <code className="code-chip">.env</code>, <code className="code-chip">saife bench</code>{' '}
              publishes automatically.
            </p>
          </EmptyState>
        </Panel>
      ) : (
        <ul className="space-y-3">
          {list.map((b) => (
            <BenchCard key={b.name} b={b} />
          ))}
        </ul>
      )}
    </main>
  )
}
