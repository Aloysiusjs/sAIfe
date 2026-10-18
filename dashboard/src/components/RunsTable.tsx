import { BadgeCheck, OctagonX, PlugZap } from 'lucide-react'
import type { RunMeta } from '../../api/_lib/core'
import { caught, countsOf, runDuration } from '../lib/stats'
import { fmtDateTime, fmtDuration, fmtInt, fmtRelative, fmtUsd, shortModel } from '../lib/format'
import { href, navigate } from '../lib/route'
import { RunStateBadge } from './StatusBadge'
import { EmptyState } from './Panel'

interface Props {
  runs: RunMeta[]
  now: number
  /** True when no runs exist at all (not just none matching the filter). */
  noRunsAtAll: boolean
}

const title = (r: RunMeta) => r.question?.trim() || (r.mode !== 'generate' ? '(answer check)' : '(no question)')

function Counts({ r }: { r: RunMeta }) {
  const c = countsOf(r)
  const n = caught(c)
  return (
    <span className="inline-flex items-center gap-3 tabular-nums">
      <span className={`inline-flex items-center gap-1 ${n ? 'font-semibold text-contradicted-fg dark:text-contradicted-bg' : 'text-ink-500 dark:text-ink-400'}`}>
        <OctagonX aria-hidden="true" className="h-3.5 w-3.5" />
        {fmtInt(n)}
        <span className="sr-only">caught</span>
      </span>
      <span className="inline-flex items-center gap-1 text-supported-fg dark:text-supported-bg">
        <BadgeCheck aria-hidden="true" className="h-3.5 w-3.5" />
        {fmtInt(c.SUPPORTED)}
        <span className="sr-only">passed</span>
      </span>
    </span>
  )
}

function Judges({ r }: { r: RunMeta }) {
  const js = Array.isArray(r.judges) ? r.judges : []
  if (!js.length) return <span className="text-ink-400">–</span>
  return (
    <span className="font-mono text-xs" title={js.join(', ')}>
      {shortModel(js[0] ?? '')}
      {js.length > 1 ? ` +${js.length - 1}` : ''}
    </span>
  )
}

export function RunsTable({ runs, now, noRunsAtAll }: Props) {
  if (runs.length === 0) {
    return noRunsAtAll ? (
      <EmptyState icon={<PlugZap aria-hidden="true" className="h-7 w-7" />} title="No runs yet">
        <p>
          Connect your local sAIfe: set <code className="code-chip">SAIFE_DASHBOARD_URL</code> and{' '}
          <code className="code-chip">SAIFE_DASHBOARD_SECRET</code> in its <code className="code-chip">.env</code>, then run a check.
          Runs appear here live. <a href="#/setup" className="font-medium text-action underline dark:text-[#8fb3f0]">Setup steps</a>
        </p>
      </EmptyState>
    ) : (
      <EmptyState title="No runs match this filter" />
    )
  }
  return (
    <>
      {/* Wide screens: a table. */}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-left text-sm">
          <thead className="label-xs">
            <tr className="border-b border-ink-100 dark:border-ink-800">
              <th scope="col" className="px-4 py-2 font-semibold">Started</th>
              <th scope="col" className="px-2 py-2 font-semibold">Question</th>
              <th scope="col" className="px-2 py-2 font-semibold">Answer</th>
              <th scope="col" className="px-2 py-2 font-semibold">Caught / passed</th>
              <th scope="col" className="px-2 py-2 font-semibold">Judges</th>
              <th scope="col" className="px-2 py-2 text-right font-semibold">Duration</th>
              <th scope="col" className="px-4 py-2 text-right font-semibold">Cost</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr
                key={r.id}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('a')) return
                  navigate({ page: 'run', id: r.id })
                }}
                className="cursor-pointer border-b border-ink-100 last:border-0 hover:bg-ink-50 dark:border-ink-800 dark:hover:bg-ink-800/60"
              >
                <td className="whitespace-nowrap px-4 py-2.5 text-ink-600 dark:text-ink-300" title={fmtDateTime(r.started_at)}>
                  {fmtRelative(r.started_at, now)}
                </td>
                <td className="max-w-[22rem] px-2 py-2.5">
                  <a href={href({ page: 'run', id: r.id })} className="line-clamp-2 font-medium text-ink-900 hover:underline dark:text-ink-100">
                    {title(r)}
                  </a>
                  {r.preset ? <span className="text-xs text-ink-500 dark:text-ink-400">{r.preset}</span> : null}
                </td>
                <td className="px-2 py-2.5">
                  <RunStateBadge state={r.state} status={r.status} />
                </td>
                <td className="px-2 py-2.5">
                  <Counts r={r} />
                </td>
                <td className="max-w-[12rem] truncate px-2 py-2.5">
                  <Judges r={r} />
                </td>
                <td className="whitespace-nowrap px-2 py-2.5 text-right tabular-nums">{fmtDuration(runDuration(r, now))}</td>
                <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums">{fmtUsd(r.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Narrow screens: stacked cards. */}
      <ul className="divide-y divide-ink-100 md:hidden dark:divide-ink-800">
        {runs.map((r) => (
          <li key={r.id}>
            <a href={href({ page: 'run', id: r.id })} className="block px-4 py-3 hover:bg-ink-50 dark:hover:bg-ink-800/60">
              <div className="flex items-start justify-between gap-2">
                <span className="line-clamp-2 min-w-0 font-medium">{title(r)}</span>
                <RunStateBadge state={r.state} status={r.status} />
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-600 dark:text-ink-300">
                <span title={fmtDateTime(r.started_at)}>{fmtRelative(r.started_at, now)}</span>
                {r.preset ? <span>{r.preset}</span> : null}
                <Counts r={r} />
                <span className="tabular-nums">{fmtDuration(runDuration(r, now))}</span>
                <span className="tabular-nums">{fmtUsd(r.cost_usd)}</span>
                <Judges r={r} />
              </div>
            </a>
          </li>
        ))}
      </ul>
    </>
  )
}
