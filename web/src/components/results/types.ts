// Mirrors bench/run.py (BenchMeta, TrapMetrics) and bench/metrics.py (Metrics), as served by
// GET /api/results: one summary per bench/results/*.json, newest first, without item lists.

export interface BenchMeta {
  set: 'seeded' | 'traps'
  preset: string
  ablate: string[]
  verifier: 'cross' | 'same'
  date: string
  n_items: number
  n_errors: number
  detectors: string[]
  models: Record<string, string>
  total_cost_usd: number
  run_name: string
}

export interface SeededMetrics {
  items: number
  clean_items: number
  errors_total: number
  confusion: { tp: number; fp: number; tn: number; fn: number }
  precision: number
  recall: number
  f1: number
  errors_detected: number
  unmapped_claims: number
  false_alarm_rate: number | null
  residual_baseline: number | null
  residual_guarded: number | null
  silent_failures: number
  latency_p50_s: number | null
  latency_p95_s: number | null
  mean_cost_usd: number | null
}

export interface TrapMetrics {
  items: number
  flag_rate: number | null
  flag_rate_by_category: Record<string, number>
  silent_failures: number
  latency_p50_s: number | null
  latency_p95_s: number | null
  mean_cost_usd: number | null
}

export interface ResultSummary {
  name: string
  meta: BenchMeta
  metrics: SeededMetrics | TrapMetrics
}

export interface SeededRun {
  name: string
  meta: BenchMeta
  metrics: SeededMetrics
}

export interface TrapRun {
  name: string
  meta: BenchMeta
  metrics: TrapMetrics
}
