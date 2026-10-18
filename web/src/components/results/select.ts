import type { ResultSummary, SeededRun, TrapRun } from './types'

const PRESET_ORDER = ['fast', 'balanced', 'strict']

const isSeeded = (r: ResultSummary): r is SeededRun => r.meta.set === 'seeded' && 'f1' in r.metrics
const isTrap = (r: ResultSummary): r is TrapRun => r.meta.set === 'traps' && 'flag_rate' in r.metrics

const byPreset = (a: { meta: { preset: string } }, b: { meta: { preset: string } }) => {
  const ia = PRESET_ORDER.indexOf(a.meta.preset)
  const ib = PRESET_ORDER.indexOf(b.meta.preset)
  return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.meta.preset.localeCompare(b.meta.preset)
}

/** The input is newest first, so the first run seen for a configuration is its latest. */
function latest<T extends ResultSummary>(runs: T[]): T[] {
  const seen = new Set<string>()
  return runs.filter((r) => {
    const key = [r.meta.set, r.meta.preset, [...r.meta.ablate].sort().join('+'), r.meta.verifier].join('|')
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export interface AblationGroup {
  preset: string
  full: SeededRun
  rows: { ablated: string; run: SeededRun }[]
}

export interface VerifierPair {
  preset: string
  cross: SeededRun
  same: SeededRun
}

export interface Selection {
  /** Full preset, cross-provider verifier: the headline runs. */
  full: SeededRun[]
  ablations: AblationGroup[]
  verifier: VerifierPair[]
  traps: TrapRun[]
  total: number
}

export function select(results: ResultSummary[]): Selection {
  const seeded = latest(results.filter(isSeeded))
  const traps = latest(results.filter(isTrap)).filter((r) => r.meta.ablate.length === 0 && r.meta.verifier === 'cross')
  const full = seeded.filter((r) => r.meta.ablate.length === 0 && r.meta.verifier === 'cross').sort(byPreset)

  const ablations: AblationGroup[] = []
  for (const base of full) {
    const rows = seeded
      .filter((r) => r.meta.preset === base.meta.preset && r.meta.verifier === 'cross' && r.meta.ablate.length > 0)
      .map((run) => ({ ablated: run.meta.ablate.join(' + '), run }))
      .sort((a, b) => a.ablated.localeCompare(b.ablated))
    if (rows.length) ablations.push({ preset: base.meta.preset, full: base, rows })
  }

  const verifier: VerifierPair[] = []
  for (const cross of full) {
    const same = seeded.find(
      (r) => r.meta.preset === cross.meta.preset && r.meta.verifier === 'same' && r.meta.ablate.length === 0,
    )
    if (same) verifier.push({ preset: cross.meta.preset, cross, same })
  }

  return { full, ablations, verifier, traps: traps.sort(byPreset), total: results.length }
}
