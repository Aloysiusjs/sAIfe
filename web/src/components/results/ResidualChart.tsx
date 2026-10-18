import { presetLabel, pct } from './format'
import type { SeededRun } from './types'

const TICKS = [0, 0.25, 0.5, 0.75, 1]

// Two series, identified by legend + a text label on every row, never by colour alone.
// Baseline is the recessive neutral (ink.400, 3.1:1 on white); sAIfe is the action blue.
const SERIES = {
  baseline: { label: 'Without sAIfe (unguarded draft)', short: 'Without sAIfe', swatch: 'bg-ink-400' },
  guarded: { label: 'With sAIfe (final answer)', short: 'With sAIfe', swatch: 'bg-action' },
} as const

function Bar({
  series,
  value,
  total,
}: {
  series: keyof typeof SERIES
  value: number | null
  total: number
}) {
  const s = SERIES[series]
  const n = value === null ? null : Math.round(value * total)
  const tip = n === null ? 'No injected errors in this run.' : `${n} of ${total} injected errors still in the answer`
  return (
    <div className="grid grid-cols-[7.5rem_1fr] items-center gap-3">
      <span className="text-xs text-ink-700">{s.short}</span>
      <div
        className="group relative h-6 pr-14 outline-none"
        role="img"
        tabIndex={0}
        aria-label={`${s.short}: ${pct(value)}. ${tip}.`}
      >
        <div className="flex h-full items-center">
          <div
            className={`h-5 shrink-0 rounded-r ${s.swatch}`}
            style={{ width: `${Math.max(0, Math.min(1, value ?? 0)) * 100}%`, minWidth: value ? 2 : 0 }}
          />
          <span className="ml-2 whitespace-nowrap text-sm font-semibold tabular-nums text-ink-900">{pct(value)}</span>
        </div>
        <span
          role="tooltip"
          className="pointer-events-none absolute -top-9 left-0 z-10 hidden whitespace-nowrap rounded-md bg-ink-900 px-2 py-1 text-xs text-white shadow-drawer group-hover:block group-focus-visible:block"
        >
          {tip}
        </span>
      </div>
    </div>
  )
}

/** Before/after bars for the residual hallucination rate, one pair per preset. Lower is better. */
export function ResidualChart({ runs }: { runs: SeededRun[] }) {
  return (
    <figure>
      <ul className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-700" aria-label="Legend">
        {Object.values(SERIES).map((s) => (
          <li key={s.label} className="inline-flex items-center gap-1.5">
            <span aria-hidden="true" className={`inline-block h-2.5 w-2.5 rounded-sm ${s.swatch}`} />
            {s.label}
          </li>
        ))}
      </ul>
      <div className="space-y-5">
        {runs.map((r) => (
          <div key={r.name}>
            <p className="mb-1.5 text-sm font-semibold text-ink-900">
              {presetLabel(r.meta.preset)}{' '}
              <span className="font-normal text-ink-500">
                · {r.metrics.errors_total} injected errors in {r.metrics.items} answers
              </span>
            </p>
            <div className="relative space-y-1.5">
              <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 left-[calc(7.5rem+0.75rem)] right-14">
                {TICKS.map((t) => (
                  <span key={t} className="absolute inset-y-0 w-px bg-ink-100" style={{ left: `${t * 100}%` }} />
                ))}
              </div>
              <Bar series="baseline" value={r.metrics.residual_baseline} total={r.metrics.errors_total} />
              <Bar series="guarded" value={r.metrics.residual_guarded} total={r.metrics.errors_total} />
            </div>
          </div>
        ))}
      </div>
      <div aria-hidden="true" className="relative ml-[calc(7.5rem+0.75rem)] mr-14 mt-2 h-4 text-2xs tabular-nums text-ink-500">
        {TICKS.map((t) => (
          <span
            key={t}
            className="absolute -translate-x-1/2 first:translate-x-0 last:-translate-x-full"
            style={{ left: `${t * 100}%` }}
          >
            {t * 100}%
          </span>
        ))}
      </div>
      <figcaption className="mt-3 max-w-[65ch] text-xs leading-relaxed text-ink-600">
        Share of injected errors whose text is still in the answer the reader sees. Lower is better. The baseline is the
        answer as given; sAIfe&apos;s figure is the answer after verification and repair.
      </figcaption>
    </figure>
  )
}
