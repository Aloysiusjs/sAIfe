import type { ResultSummary, SeededMetrics } from './types'

/**
 * ILLUSTRATIVE ONLY. These numbers are invented to show the page layout and are never
 * presented as benchmark results: the page wraps them in an "Example layout" banner.
 */
const metrics = (p: number, r: number, base: number, guarded: number, fa: number): SeededMetrics => ({
  items: 61,
  clean_items: 12,
  errors_total: 100,
  confusion: { tp: 0, fp: 0, tn: 0, fn: 0 },
  precision: p,
  recall: r,
  f1: p + r === 0 ? 0 : (2 * p * r) / (p + r),
  errors_detected: Math.round(r * 100),
  unmapped_claims: 0,
  false_alarm_rate: fa,
  residual_baseline: base,
  residual_guarded: guarded,
  silent_failures: 0,
  latency_p50_s: null,
  latency_p95_s: null,
  mean_cost_usd: null,
})

const meta = (preset: string, ablate: string[] = [], verifier: 'cross' | 'same' = 'cross') => ({
  set: 'seeded' as const,
  preset,
  ablate,
  verifier,
  date: 'example',
  n_items: 61,
  n_errors: 0,
  detectors: [],
  models: {},
  total_cost_usd: 0,
  run_name: `example-${preset}`,
})

export const SAMPLE_RESULTS: ResultSummary[] = [
  { name: 'ex-fast', meta: meta('fast'), metrics: metrics(0.7, 0.5, 1, 0.5, 0.2) },
  { name: 'ex-balanced', meta: meta('balanced'), metrics: metrics(0.8, 0.7, 1, 0.3, 0.1) },
  { name: 'ex-balanced-g', meta: meta('balanced', ['grounding']), metrics: metrics(0.6, 0.3, 1, 0.7, 0.1) },
  { name: 'ex-balanced-c', meta: meta('balanced', ['consistency']), metrics: metrics(0.8, 0.6, 1, 0.4, 0.1) },
  { name: 'ex-balanced-same', meta: meta('balanced', [], 'same'), metrics: metrics(0.8, 0.6, 1, 0.4, 0.1) },
]
