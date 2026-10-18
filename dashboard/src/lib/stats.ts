/** Aggregations over run summaries for the overview page. Pure functions. */

import type { ClaimCounts, ClaimStatus, RunMeta } from '../../api/_lib/core'
import { CLAIM_STATUSES, zeroCounts } from '../../api/_lib/core'

export type WindowKey = '24h' | '7d' | '30d'
export const WINDOWS: { key: WindowKey; label: string; seconds: number }[] = [
  { key: '24h', label: '24 h', seconds: 86400 },
  { key: '7d', label: '7 days', seconds: 7 * 86400 },
  { key: '30d', label: '30 days', seconds: 30 * 86400 },
]

export function windowSeconds(key: WindowKey): number {
  return WINDOWS.find((w) => w.key === key)?.seconds ?? 7 * 86400
}

/** RunMeta.counts may be missing a key in malformed data; read through this. */
export function countsOf(run: RunMeta): ClaimCounts {
  const c = zeroCounts()
  const src = run.counts as Partial<Record<ClaimStatus, unknown>> | undefined
  if (src) for (const s of CLAIM_STATUSES) c[s] = typeof src[s] === 'number' ? (src[s] as number) : 0
  return c
}

export const caught = (c: ClaimCounts): number => c.CONTRADICTED + c.UNSUPPORTED

export interface Kpis {
  runs: number
  caught: number
  passed: number
  unverifiable: number
  likely: number
  verified: number
  caution: number
  blocked: number
  failed: number
  running: number
  claims: number
}

export function kpis(runs: RunMeta[]): Kpis {
  const k: Kpis = { runs: runs.length, caught: 0, passed: 0, unverifiable: 0, likely: 0, verified: 0, caution: 0, blocked: 0, failed: 0, running: 0, claims: 0 }
  for (const r of runs) {
    const c = countsOf(r)
    k.caught += caught(c)
    k.passed += c.SUPPORTED
    k.unverifiable += c.UNVERIFIABLE
    k.likely += c.LIKELY
    k.claims += CLAIM_STATUSES.reduce((n, s) => n + c[s], 0)
    if (r.status === 'VERIFIED') k.verified += 1
    else if (r.status === 'CAUTION') k.caution += 1
    else if (r.status === 'BLOCKED') k.blocked += 1
    if (r.state === 'running') k.running += 1
    if (r.state === 'failed') k.failed += 1
  }
  return k
}

export interface Bucket {
  start: number // unix seconds
  counts: ClaimCounts
  runs: number
}

/** Claims per bucket for the window: hourly for 24 h, daily (local midnight) otherwise. */
export function buckets(runs: RunMeta[], key: WindowKey, now: number): { buckets: Bucket[]; unit: 'hour' | 'day' } {
  const unit = key === '24h' ? 'hour' : 'day'
  const n = key === '24h' ? 24 : key === '7d' ? 7 : 30
  const starts: number[] = []
  const d = new Date(now * 1000)
  if (unit === 'hour') d.setMinutes(0, 0, 0)
  else d.setHours(0, 0, 0, 0)
  for (let i = n - 1; i >= 0; i--) {
    const b = new Date(d)
    if (unit === 'hour') b.setHours(d.getHours() - i)
    else b.setDate(d.getDate() - i)
    starts.push(b.getTime() / 1000)
  }
  const out: Bucket[] = starts.map((start) => ({ start, counts: zeroCounts(), runs: 0 }))
  for (const r of runs) {
    let idx = -1
    for (let i = starts.length - 1; i >= 0; i--) {
      if (r.started_at >= (starts[i] ?? Infinity)) {
        idx = i
        break
      }
    }
    const b = out[idx]
    if (!b) continue
    b.runs += 1
    const c = countsOf(r)
    for (const s of CLAIM_STATUSES) b.counts[s] += c[s]
  }
  return { buckets: out, unit }
}

export interface JudgeStat {
  model: string
  runs: number
}

export function judgeStats(runs: RunMeta[]): JudgeStat[] {
  const m = new Map<string, number>()
  for (const r of runs) for (const j of Array.isArray(r.judges) ? r.judges : []) m.set(j, (m.get(j) ?? 0) + 1)
  return [...m.entries()].map(([model, n]) => ({ model, runs: n })).sort((a, b) => b.runs - a.runs || a.model.localeCompare(b.model))
}

export type RunFilter = 'all' | 'caught' | 'blocked' | 'caution' | 'verified' | 'running' | 'failed'

export const RUN_FILTERS: { key: RunFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'caught', label: 'Hallucinations caught' },
  { key: 'blocked', label: 'Blocked' },
  { key: 'caution', label: 'Caution' },
  { key: 'verified', label: 'Verified' },
  { key: 'running', label: 'Running' },
  { key: 'failed', label: 'Failed' },
]

export function matchesFilter(r: RunMeta, f: RunFilter): boolean {
  switch (f) {
    case 'caught':
      return caught(countsOf(r)) > 0
    case 'blocked':
      return r.status === 'BLOCKED'
    case 'caution':
      return r.status === 'CAUTION'
    case 'verified':
      return r.status === 'VERIFIED'
    case 'running':
      return r.state === 'running'
    case 'failed':
      return r.state === 'failed'
    default:
      return true
  }
}

/** Duration of a run in seconds: reported wall time, else elapsed. */
export function runDuration(r: RunMeta, now: number): number | null {
  if (typeof r.wall_time_s === 'number') return r.wall_time_s
  if (r.state === 'running') return Math.max(0, now - r.started_at)
  if (typeof r.completed_at === 'number') return Math.max(0, r.completed_at - r.started_at)
  return null
}
