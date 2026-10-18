import { Gavel, Shuffle, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import type { RunMeta } from '../../api/_lib/core'
import { judgeStats } from '../lib/stats'
import { fmtInt, shortModel } from '../lib/format'
import { href } from '../lib/route'
import { EmptyState, Panel } from './Panel'

function runLabel(r: RunMeta): string {
  return r.question?.trim() || '(answer check)'
}

export function JudgesPanel({ runs }: { runs: RunMeta[] }) {
  const stats = judgeStats(runs)
  const max = Math.max(1, ...stats.map((s) => s.runs))
  const fallbacks = runs.filter((r) => (r.fallbacks ?? 0) > 0)
  const sameFamily = runs.filter((r) => r.verifier_diversity === false)
  return (
    <Panel title="Judges" id="judges">
      {stats.length === 0 ? (
        <EmptyState icon={<Gavel aria-hidden="true" className="h-6 w-6" />} title="No verdicts in this window" />
      ) : (
        <ul className="space-y-2.5">
          {stats.map((s) => (
            <li key={s.model} className="min-w-0">
              <div className="flex items-baseline justify-between gap-2 text-sm">
                <span className="truncate font-mono text-xs" title={s.model}>
                  {shortModel(s.model)}
                  <span className="ml-1 text-ink-400">{s.model.includes('/') ? s.model.split('/')[0] : ''}</span>
                </span>
                <span className="shrink-0 tabular-nums text-ink-600 dark:text-ink-300">
                  {fmtInt(s.runs)} {s.runs === 1 ? 'run' : 'runs'}
                </span>
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-ink-100 dark:bg-ink-800" aria-hidden="true">
                <div className="h-1.5 rounded-full bg-action dark:bg-[#8fb3f0]" style={{ width: `${(s.runs / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 space-y-3 border-t border-ink-100 pt-3 text-sm dark:border-ink-800">
        <FlagList
          icon={<Shuffle aria-hidden="true" className="h-4 w-4 text-likely-line" />}
          title="Fell back to Claude"
          hint="The verifier was unavailable, so a Claude model judged instead. Disclosed in the report."
          runs={fallbacks}
          count={(r) => `${fmtInt(r.fallbacks)} ${r.fallbacks === 1 ? 'fallback' : 'fallbacks'}`}
        />
        <FlagList
          icon={<TriangleAlert aria-hidden="true" className="h-4 w-4 text-unsupported-line" />}
          title="No verifier diversity"
          hint="The judge came from the same model family as the answer, so errors may be shared."
          runs={sameFamily}
        />
      </div>
    </Panel>
  )
}

function FlagList({
  icon,
  title,
  hint,
  runs,
  count,
}: {
  icon: ReactNode
  title: string
  hint: string
  runs: RunMeta[]
  count?: (r: RunMeta) => string
}) {
  return (
    <div>
      <p className="flex items-center gap-1.5 font-medium">
        {icon}
        {title}
        <span className="ml-auto tabular-nums text-ink-600 dark:text-ink-300">
          {fmtInt(runs.length)} {runs.length === 1 ? 'run' : 'runs'}
        </span>
      </p>
      <p className="mt-0.5 text-xs text-ink-500 dark:text-ink-400">{hint}</p>
      {runs.length ? (
        <ul className="mt-1 space-y-0.5 text-xs">
          {runs.slice(0, 5).map((r) => (
            <li key={r.id} className="flex min-w-0 gap-2">
              <a href={href({ page: 'run', id: r.id })} className="truncate text-action underline-offset-2 hover:underline dark:text-[#8fb3f0]">
                {runLabel(r)}
              </a>
              {count ? <span className="shrink-0 text-ink-500 dark:text-ink-400">{count(r)}</span> : null}
            </li>
          ))}
          {runs.length > 5 ? <li className="text-ink-500 dark:text-ink-400">and {fmtInt(runs.length - 5)} more</li> : null}
        </ul>
      ) : null}
    </div>
  )
}
