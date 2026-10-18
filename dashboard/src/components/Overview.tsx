import { useEffect, useMemo, useState } from 'react'
import { getRuns } from '../api'
import { usePolling } from '../lib/usePolling'
import { buckets, kpis, matchesFilter, RUN_FILTERS, WINDOWS, windowSeconds, type RunFilter, type WindowKey } from '../lib/stats'
import { fmtInt } from '../lib/format'
import type { Connection } from './Header'
import { ClaimsChart } from './ClaimsChart'
import { Freshness } from './Freshness'
import { JudgesPanel } from './JudgesPanel'
import { KpiTiles } from './KpiTiles'
import { Notice, Panel, Spinner } from './Panel'
import { RunsTable } from './RunsTable'

interface Props {
  password: string
  onApiError: (e: unknown) => boolean
  onConnection: (c: Connection) => void
}

export function Overview({ password, onApiError, onConnection }: Props) {
  const [win, setWin] = useState<WindowKey>('7d')
  const [filter, setFilter] = useState<RunFilter>('all')
  const polled = usePolling('runs', (signal) => getRuns(password, signal), 5000)
  const { data, error } = polled

  useEffect(() => {
    if (error && onApiError(error)) return
    onConnection(error ? (data ? 'stale' : 'offline') : data ? 'ok' : 'unknown')
  }, [error, data, onApiError, onConnection])

  const now = data?.now ?? Date.now() / 1000
  const inWindow = useMemo(() => {
    const cutoff = now - windowSeconds(win)
    return (data?.runs ?? []).filter((r) => r.started_at >= cutoff || r.state === 'running')
  }, [data, win, now])
  const k = useMemo(() => kpis(inWindow), [inWindow])
  const series = useMemo(() => buckets(inWindow, win, now), [inWindow, win, now])
  const shown = inWindow.filter((r) => matchesFilter(r, filter))

  if (!data) {
    return (
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {error ? (
          <Notice tone="error">Could not load runs: {error instanceof Error ? error.message : 'unknown error'}</Notice>
        ) : (
          <Spinner label="Loading runs" />
        )}
      </main>
    )
  }

  return (
    <main className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Overview</h1>
        <div className="flex flex-wrap items-center gap-3">
          <div role="group" aria-label="Time window" className="inline-flex rounded-md border border-ink-200 bg-white p-0.5 dark:border-ink-700 dark:bg-ink-900">
            {WINDOWS.map((w) => (
              <button
                key={w.key}
                type="button"
                aria-pressed={win === w.key}
                onClick={() => setWin(w.key)}
                className={`rounded px-2.5 py-1 text-xs font-medium ${
                  win === w.key ? 'bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-950' : 'text-ink-600 hover:text-ink-900 dark:text-ink-300 dark:hover:text-white'
                }`}
              >
                {w.label}
              </button>
            ))}
          </div>
          <Freshness updatedAt={polled.updatedAt} loading={polled.loading} stale={!!error} onRefresh={polled.refresh} />
        </div>
      </div>

      <KpiTiles k={k} />

      <div className="grid min-w-0 gap-4 lg:grid-cols-3">
        <Panel title={`Claims per ${series.unit}`} id="chart" className="lg:col-span-2">
          <ClaimsChart buckets={series.buckets} unit={series.unit} />
        </Panel>
        <JudgesPanel runs={inWindow} />
      </div>

      <Panel
        title={`Runs (${fmtInt(shown.length)})`}
        id="runs"
        bodyClassName=""
        actions={
          <div role="group" aria-label="Filter runs" className="flex flex-wrap gap-1.5">
            {RUN_FILTERS.map((f) => {
              const n = inWindow.filter((r) => matchesFilter(r, f.key)).length
              return (
                <button
                  key={f.key}
                  type="button"
                  aria-pressed={filter === f.key}
                  onClick={() => setFilter(f.key)}
                  className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                    filter === f.key
                      ? 'border-ink-900 bg-ink-900 text-white dark:border-ink-100 dark:bg-ink-100 dark:text-ink-950'
                      : 'border-ink-200 text-ink-600 hover:border-ink-400 dark:border-ink-700 dark:text-ink-300'
                  }`}
                >
                  {f.label} <span className="tabular-nums opacity-70">{fmtInt(n)}</span>
                </button>
              )
            })}
          </div>
        }
      >
        <RunsTable runs={shown} now={now} noRunsAtAll={(data.runs ?? []).length === 0} />
      </Panel>
    </main>
  )
}
