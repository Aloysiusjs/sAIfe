import { ArrowDownRight, ArrowUpRight, BadgeCheck, Minus, OctagonX } from 'lucide-react'

export const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? 'n/a' : `${(100 * x).toFixed(digits)}%`

export const score = (x: number | null | undefined) => (x === null || x === undefined ? 'n/a' : x.toFixed(2))

export const PRESET_LABEL: Record<string, string> = { fast: 'Fast', balanced: 'Balanced', strict: 'Strict' }
export const presetLabel = (p: string) => PRESET_LABEL[p] ?? p

/**
 * A change against a reference value, shown as a signed number plus an arrow and a word,
 * so better/worse never depends on colour alone. `higherIsBetter` says which way is good.
 */
export function Delta({
  value,
  base,
  higherIsBetter,
  kind,
}: {
  value: number | null
  base: number | null
  higherIsBetter: boolean
  kind: 'pct' | 'score'
}) {
  if (value === null || base === null) return <span className="text-ink-500">n/a</span>
  const d = value - base
  const shown = kind === 'pct' ? `${(100 * d).toFixed(1)} pts` : d.toFixed(2)
  if (Math.abs(d) < (kind === 'pct' ? 0.0005 : 0.005)) {
    return (
      <span className="inline-flex items-center gap-1 text-ink-600">
        <Minus aria-hidden="true" className="h-3.5 w-3.5" />
        no change
      </span>
    )
  }
  const better = d > 0 === higherIsBetter
  const Icon = d > 0 ? ArrowUpRight : ArrowDownRight
  return (
    <span className={`inline-flex items-center gap-1 ${better ? 'text-supported-fg' : 'text-contradicted-fg'}`}>
      <Icon aria-hidden="true" className="h-3.5 w-3.5" />
      <span>
        {d > 0 ? '+' : '−'}
        {shown.replace('-', '')}
      </span>
      <span className="text-2xs font-semibold uppercase tracking-wide">{better ? 'better' : 'worse'}</span>
    </span>
  )
}

/** Silent failures must be 0; a non-zero count is a safety bug, flagged with icon and words. */
export function SilentFailures({ n }: { n: number }) {
  return n === 0 ? (
    <span className="inline-flex items-center gap-1 text-supported-fg">
      <BadgeCheck aria-hidden="true" className="h-3.5 w-3.5" />0
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 font-semibold text-contradicted-fg">
      <OctagonX aria-hidden="true" className="h-3.5 w-3.5" />
      {n} (safety bug)
    </span>
  )
}
