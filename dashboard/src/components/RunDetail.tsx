import { ArrowLeft, ChevronRight, Radio } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { getRun, NotFoundError } from '../api'
import type { RunMeta } from '../../api/_lib/core'
import { usePolling } from '../lib/usePolling'
import { viewFromEvents, viewFromReport, isFlagged, type RunView } from '../lib/report'
import { runDuration } from '../lib/stats'
import { STOP_CAUSE } from '../lib/status'
import { fmtDateTime, fmtDuration, fmtInt, fmtPct, fmtRelative, fmtUsd, shortModel } from '../lib/format'
import type { Connection } from './Header'
import { ClaimCard } from './ClaimCard'
import { Freshness } from './Freshness'
import { EmptyState, Notice, Panel, Spinner } from './Panel'
import { AlertList, RepairList } from './RepairsAlerts'
import { RunStateBadge } from './StatusBadge'

interface Props {
  id: string
  password: string
  onApiError: (e: unknown) => boolean
  onConnection: (c: Connection) => void
}

function Back() {
  return (
    <a href="#/" className="inline-flex items-center gap-1 rounded text-sm font-medium text-action hover:underline dark:text-[#8fb3f0]">
      <ArrowLeft aria-hidden="true" className="h-4 w-4" />
      All runs
    </a>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="label-xs">{label}</dt>
      <dd className="mt-0.5 break-words text-sm tabular-nums">{children}</dd>
    </div>
  )
}

function Facts({ meta, view, now }: { meta: RunMeta; view: RunView; now: number }) {
  const tokens =
    view.inputTokens !== null && view.outputTokens !== null
      ? `${fmtInt(view.inputTokens + view.outputTokens)} (${fmtInt(view.inputTokens)} in · ${fmtInt(view.outputTokens)} out)`
      : fmtInt(meta.tokens)
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
      <Fact label="Started">
        <span title={fmtDateTime(meta.started_at)}>{fmtRelative(meta.started_at, now)}</span>
      </Fact>
      <Fact label="Preset">{meta.preset ?? '–'}</Fact>
      <Fact label="Mode">{meta.mode ?? '–'}</Fact>
      <Fact label="Duration">{fmtDuration(runDuration(meta, now))}</Fact>
      <Fact label="Passes">{fmtInt(view.passes || meta.passes)}</Fact>
      <Fact label="Tokens">{tokens}</Fact>
      <Fact label="Cost">{fmtUsd(meta.cost_usd)}</Fact>
      <Fact label="Risk">{meta.risk !== null ? fmtPct(meta.risk) : '–'}</Fact>
      <Fact label="Stop cause">{meta.stop_cause ? (STOP_CAUSE[meta.stop_cause] ?? meta.stop_cause) : '–'}</Fact>
      <Fact label="Sources">{fmtInt(meta.n_sources)}</Fact>
    </dl>
  )
}

export function RunDetail({ id, password, onApiError, onConnection }: Props) {
  const [running, setRunning] = useState(false)
  // Live runs refresh every 2 s until they finish; finished runs load once.
  const polled = usePolling(`run:${id}`, (signal) => getRun(password, id, signal), running ? 2000 : null)
  const latest = polled.data
  const err = polled.error
  useEffect(() => {
    setRunning(latest?.meta.state === 'running')
  }, [latest])

  useEffect(() => {
    if (err && onApiError(err)) return
    onConnection(err ? (latest ? 'stale' : 'offline') : latest ? 'ok' : 'unknown')
  }, [err, latest, onApiError, onConnection])

  const view = useMemo(() => {
    if (!latest) return null
    return latest.report ? viewFromReport(latest.report) : viewFromEvents(latest.events)
  }, [latest])

  if (!latest || !view) {
    return (
      <main className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-6 sm:px-6">
        <Back />
        {err instanceof NotFoundError ? (
          <EmptyState title="Run not found">This run does not exist or has expired from storage.</EmptyState>
        ) : err ? (
          <Notice tone="error">Could not load this run: {err instanceof Error ? err.message : 'unknown error'}</Notice>
        ) : (
          <Spinner label="Loading run" />
        )}
      </main>
    )
  }

  const { meta, now } = latest
  const isRunning = meta.state === 'running'
  const flagged = view.claims.filter((c) => isFlagged(c.status)).length
  const question = view.question ?? meta.question
  const answer = view.finalAnswer ?? meta.final_answer

  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Back />
        <Freshness updatedAt={polled.updatedAt} loading={polled.loading} stale={!!err} onRefresh={polled.refresh} />
      </div>

      <Panel>
        <div className="flex flex-wrap items-center gap-3">
          <RunStateBadge state={meta.state} status={meta.status} size="md" />
          <h1 className="min-w-0 flex-1 basis-64 break-words text-lg font-semibold">{question?.trim() || '(answer check)'}</h1>
        </div>
        <p className="mt-1 break-all font-mono text-xs text-ink-500 dark:text-ink-400">run {meta.id}</p>
        <div className="mt-4">
          <Facts meta={meta} view={view} now={now} />
        </div>
        {meta.judges?.length || view.byModel.length ? (
          <p className="mt-3 text-xs text-ink-500 dark:text-ink-400">
            Judges: <span className="font-mono">{(meta.judges ?? []).map(shortModel).join(', ') || '–'}</span>
            {view.byModel.length ? (
              <>
                {' '}
                · Models used: <span className="font-mono">{view.byModel.map(shortModel).join(', ')}</span>
              </>
            ) : null}
            {meta.verifier_diversity === false ? ' · no verifier diversity' : ''}
          </p>
        ) : null}
        {meta.error ? (
          <div className="mt-3">
            <Notice tone="error">Run failed: {meta.error}</Notice>
          </div>
        ) : null}
      </Panel>

      {isRunning ? (
        <Notice tone="info">
          <Radio aria-hidden="true" className="h-4 w-4 shrink-0 text-action dark:text-[#8fb3f0]" />
          <span>
            Live: {fmtInt(view.claims.length)} claims extracted, {fmtInt(view.claims.filter((c) => c.status !== null).length)} judged,{' '}
            {fmtInt(view.repairs.length)} repairs so far. Updating every 2 seconds.
          </span>
        </Notice>
      ) : null}

      {view.alerts.length ? (
        <section aria-labelledby="alerts-h" className="space-y-2">
          <h2 id="alerts-h" className="text-sm font-semibold">
            Alerts
          </h2>
          <AlertList alerts={view.alerts} />
        </section>
      ) : null}

      {answer ? (
        <Panel title={isRunning ? 'Current draft' : 'Final answer'}>
          <p className="whitespace-pre-wrap break-words font-serif leading-relaxed">{answer}</p>
        </Panel>
      ) : null}

      <section aria-labelledby="claims-h" className="space-y-2">
        <h2 id="claims-h" className="text-sm font-semibold">
          Claims <span className="font-normal text-ink-500 dark:text-ink-400">({fmtInt(view.claims.length)}, {fmtInt(flagged)} flagged)</span>
        </h2>
        {view.claims.length ? (
          <ul className="space-y-3">
            {view.claims.map((c) => (
              <ClaimCard key={c.id} c={c} />
            ))}
          </ul>
        ) : (
          <div className="panel">
            <EmptyState title={isRunning ? 'Waiting for claims' : 'No claims'}>
              {isRunning ? 'Claims appear here as they are extracted.' : 'This run produced no claims to check.'}
            </EmptyState>
          </div>
        )}
      </section>

      {view.repairs.length ? (
        <section aria-labelledby="repairs-h" className="space-y-2">
          <h2 id="repairs-h" className="text-sm font-semibold">
            Repairs <span className="font-normal text-ink-500 dark:text-ink-400">({fmtInt(view.repairs.length)})</span>
          </h2>
          <RepairList repairs={view.repairs} />
        </section>
      ) : null}

      {view.fallbacks.length ? (
        <Panel title="Fallbacks">
          <ul className="space-y-1 text-sm">
            {view.fallbacks.map((f, i) => (
              <li key={i} className="break-words">
                <span className="font-medium">{f.role}</span>: <span className="font-mono text-xs">{f.from}</span> →{' '}
                <span className="font-mono text-xs">{f.to}</span> <span className="text-ink-500 dark:text-ink-400">({f.reason})</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {view.originalDraft && view.originalDraft !== answer ? (
        <details className="panel group">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-lg px-4 py-2.5 text-sm font-semibold">
            <ChevronRight aria-hidden="true" className="h-4 w-4 transition-transform group-open:rotate-90" />
            Original draft
          </summary>
          <p className="whitespace-pre-wrap break-words border-t border-ink-100 px-4 py-3 font-serif leading-relaxed text-ink-700 dark:border-ink-800 dark:text-ink-300">
            {view.originalDraft}
          </p>
        </details>
      ) : null}
    </main>
  )
}
