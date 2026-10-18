/**
 * Pure logic shared by the dashboard's Vercel functions, the frontend and the tests.
 * No imports, no I/O: everything here is deterministic and unit-tested with `node --test`.
 *
 * Data flow: sAIfe's WebhookSink POSTs every pipeline event to /api/ingest, signed with
 * HMAC-SHA256 (`X-Saife-Signature: sha256=<hex>` over the exact body bytes). The ingest
 * function verifies the signature, then folds the event into a small per-run summary
 * (`RunMeta`) with `applyEvent`. `run.completed` carries the full report, so the final
 * summary is computed from the report, not from the live counts.
 */

export const EVENT_TYPES = [
  'run.started',
  'pass.started',
  'claim.extracted',
  'claim.verdict',
  'repair.applied',
  'alert.raised',
  'run.completed',
  'run.failed',
] as const
export type EventType = (typeof EVENT_TYPES)[number]

export const CLAIM_STATUSES = ['SUPPORTED', 'LIKELY', 'UNSUPPORTED', 'CONTRADICTED', 'UNVERIFIABLE'] as const
export type ClaimStatus = (typeof CLAIM_STATUSES)[number]
export type AnswerStatus = 'VERIFIED' | 'CAUTION' | 'BLOCKED'
export type Severity = 'info' | 'warning' | 'high'

export interface SaifeEvent {
  type: EventType
  run_id: string
  seq: number
  ts: number // unix seconds
  data: Record<string, unknown>
}

export type ClaimCounts = Record<ClaimStatus, number>

export interface RunMeta {
  id: string
  state: 'running' | 'completed' | 'failed'
  started_at: number // unix seconds
  updated_at: number
  completed_at: number | null
  question: string | null
  preset: string | null
  mode: string | null // "generate" | "check"
  n_sources: number
  passes: number
  /** Latest status per claim while the run is in flight; cleared when it completes. */
  live: Record<string, ClaimStatus>
  counts: ClaimCounts
  repairs: number
  alerts: Record<Severity, number>
  /** Judge models that issued verdicts ("provider/model"), deterministic checks excluded. */
  judges: string[]
  status: AnswerStatus | null
  risk: number | null
  stop_cause: string | null
  models: string[]
  tokens: number | null
  cost_usd: number | null
  wall_time_s: number | null
  verifier_diversity: boolean | null
  fallbacks: number
  final_answer: string | null
  error: string | null
}

export interface BenchSummary {
  name: string
  date: string
  set: string
  preset: string
  ablate: string[]
  verifier: string
  n_items: number
  n_errors: number
  detectors: string[]
  models: Record<string, string>
  total_cost_usd: number | null
  metrics: Record<string, unknown>
  received_at: number
}

export const RUN_ID_RE = /^[A-Za-z0-9_-]{1,64}$/
export const BENCH_NAME_RE = /^[A-Za-z0-9._-]{1,120}$/
export const MAX_BODY_BYTES = 1_000_000
export const FINAL_ANSWER_CHARS = 600

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export function zeroCounts(): ClaimCounts {
  return { SUPPORTED: 0, LIKELY: 0, UNSUPPORTED: 0, CONTRADICTED: 0, UNVERIFIABLE: 0 }
}

function isClaimStatus(v: unknown): v is ClaimStatus {
  return typeof v === 'string' && (CLAIM_STATUSES as readonly string[]).includes(v)
}

/** Parse and validate an ingested event. Returns null for anything malformed. */
export function parseEvent(raw: unknown): SaifeEvent | null {
  if (!isObj(raw)) return null
  const type = raw.type
  const runId = raw.run_id
  if (typeof type !== 'string' || !(EVENT_TYPES as readonly string[]).includes(type)) return null
  if (typeof runId !== 'string' || !RUN_ID_RE.test(runId)) return null
  const seq = num(raw.seq)
  const ts = num(raw.ts)
  if (seq === null || seq < 0 || !Number.isInteger(seq) || ts === null) return null
  return { type: type as EventType, run_id: runId, seq, ts, data: isObj(raw.data) ? raw.data : {} }
}

export function emptyMeta(id: string, ts: number): RunMeta {
  return {
    id,
    state: 'running',
    started_at: ts,
    updated_at: ts,
    completed_at: null,
    question: null,
    preset: null,
    mode: null,
    n_sources: 0,
    passes: 0,
    live: {},
    counts: zeroCounts(),
    repairs: 0,
    alerts: { info: 0, warning: 0, high: 0 },
    judges: [],
    status: null,
    risk: null,
    stop_cause: null,
    models: [],
    tokens: null,
    cost_usd: null,
    wall_time_s: null,
    verifier_diversity: null,
    fallbacks: 0,
    final_answer: null,
    error: null,
  }
}

function countLive(live: Record<string, ClaimStatus>): ClaimCounts {
  const counts = zeroCounts()
  for (const s of Object.values(live)) counts[s] += 1
  return counts
}

function judgesOf(verdicts: unknown, into: Set<string>): void {
  if (!Array.isArray(verdicts)) return
  for (const v of verdicts) {
    if (!isObj(v) || v.detector === 'deterministic') continue
    const model = str(v.model)
    if (model) into.add(model)
  }
}

function isSeverity(v: unknown): v is Severity {
  return v === 'info' || v === 'warning' || v === 'high'
}

/** Final summary from the report carried by run.completed. */
export function summarizeReport(meta: RunMeta, report: Record<string, unknown>, ts: number): RunMeta {
  const counts = zeroCounts()
  const judges = new Set(meta.judges)
  for (const c of Array.isArray(report.claims) ? report.claims : []) {
    if (!isObj(c)) continue
    if (isClaimStatus(c.status)) counts[c.status] += 1
    judgesOf(c.verdicts, judges)
  }
  const alerts: Record<Severity, number> = { info: 0, warning: 0, high: 0 }
  for (const a of Array.isArray(report.alerts) ? report.alerts : []) {
    if (isObj(a) && isSeverity(a.severity)) alerts[a.severity] += 1
  }
  const usage = isObj(report.usage) ? report.usage : {}
  const byModel = isObj(usage.by_model) ? Object.keys(usage.by_model) : []
  const tokensIn = num(usage.input_tokens)
  const tokensOut = num(usage.output_tokens)
  const status = str(report.status)
  const finalAnswer = str(report.final_answer)
  return {
    ...meta,
    state: 'completed',
    completed_at: ts,
    updated_at: ts,
    live: {},
    counts,
    judges: [...judges].sort(),
    repairs: Array.isArray(report.repairs) ? report.repairs.length : meta.repairs,
    alerts,
    passes: Array.isArray(report.passes) ? report.passes.length : meta.passes,
    status: status === 'VERIFIED' || status === 'CAUTION' || status === 'BLOCKED' ? status : null,
    risk: num(report.risk),
    stop_cause: str(report.stop_cause),
    question: str(report.question) ?? meta.question,
    preset: str(report.preset) ?? meta.preset,
    models: byModel.sort(),
    tokens: tokensIn !== null && tokensOut !== null ? tokensIn + tokensOut : null,
    cost_usd: num(usage.cost_usd),
    wall_time_s: num(usage.wall_time_s),
    verifier_diversity: typeof report.verifier_diversity === 'boolean' ? report.verifier_diversity : null,
    fallbacks: Array.isArray(report.fallbacks) ? report.fallbacks.length : 0,
    final_answer: finalAnswer === null ? null : finalAnswer.slice(0, FINAL_ANSWER_CHARS),
  }
}

/** Fold one event into the run summary. Pure: returns a new object. */
export function applyEvent(prev: RunMeta | null, ev: SaifeEvent): RunMeta {
  const meta: RunMeta = prev ? { ...prev, updated_at: Math.max(prev.updated_at, ev.ts) } : emptyMeta(ev.run_id, ev.ts)
  const d = ev.data
  switch (ev.type) {
    case 'run.started': {
      const sources = Array.isArray(d.sources) ? d.sources.length : 0
      return { ...meta, started_at: ev.ts, question: str(d.question), preset: str(d.preset), mode: str(d.mode), n_sources: sources }
    }
    case 'pass.started': {
      const index = num(d.index)
      return index === null ? meta : { ...meta, passes: Math.max(meta.passes, index + 1) }
    }
    case 'claim.verdict': {
      if (meta.state !== 'running') return meta
      const result = isObj(d.result) ? d.result : null
      const claim = result && isObj(result.claim) ? result.claim : null
      const id = claim ? str(claim.id) : null
      if (!result || !id || !isClaimStatus(result.status)) return meta
      const live = { ...meta.live, [id]: result.status }
      const judges = new Set(meta.judges)
      judgesOf(result.verdicts, judges)
      return { ...meta, live, counts: countLive(live), judges: [...judges].sort() }
    }
    case 'repair.applied':
      return meta.state === 'running' ? { ...meta, repairs: meta.repairs + 1 } : meta
    case 'alert.raised': {
      const alert = isObj(d.alert) ? d.alert : null
      if (meta.state !== 'running' || !alert || !isSeverity(alert.severity)) return meta
      return { ...meta, alerts: { ...meta.alerts, [alert.severity]: meta.alerts[alert.severity] + 1 } }
    }
    case 'run.completed':
      return isObj(d.report)
        ? summarizeReport(meta, d.report, ev.ts)
        : { ...meta, state: 'completed', completed_at: ev.ts, live: {} }
    case 'run.failed':
      return { ...meta, state: 'failed', completed_at: ev.ts, live: {}, error: (str(d.error) ?? 'run failed').slice(0, 500) }
    default:
      return meta
  }
}

/** Validate a benchmark result (bench/results/*.json, items optional) into a summary. */
export function parseBench(raw: unknown, receivedAt: number): BenchSummary | null {
  if (!isObj(raw) || !isObj(raw.meta) || !isObj(raw.metrics)) return null
  const m = raw.meta
  const name = str(m.run_name)
  if (!name || !BENCH_NAME_RE.test(name)) return null
  const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
  const models: Record<string, string> = {}
  if (isObj(m.models)) for (const [k, v] of Object.entries(m.models)) if (typeof v === 'string') models[k] = v
  const { silent_failure_ids: _ids, ...metrics } = raw.metrics
  return {
    name,
    date: str(m.date) ?? '',
    set: str(m.set) ?? '',
    preset: str(m.preset) ?? '',
    ablate: strings(m.ablate),
    verifier: str(m.verifier) ?? '',
    n_items: num(m.n_items) ?? 0,
    n_errors: num(m.n_errors) ?? 0,
    detectors: strings(m.detectors),
    models,
    total_cost_usd: num(m.total_cost_usd),
    metrics,
    received_at: receivedAt,
  }
}

// --- Crypto (Web Crypto: available in Vercel's Node runtime, browsers and node --test) ---

const encoder = new TextEncoder()

function toHex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** Constant-time string comparison (length is not secret). */
export function safeEqual(a: string, b: string): boolean {
  const ab = encoder.encode(a)
  const bb = encoder.encode(b)
  let diff = ab.length ^ bb.length
  for (let i = 0; i < Math.max(ab.length, bb.length); i++) diff |= (ab[i] ?? 0) ^ (bb[i] ?? 0)
  return diff === 0
}

export async function hmacHex(secret: string, body: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, [
    'sign',
  ])
  return toHex(await crypto.subtle.sign('HMAC', key, encoder.encode(body)))
}

/** Verify `X-Saife-Signature: sha256=<hex>` against the exact body text. */
export async function verifySignature(secret: string, body: string, header: string | null): Promise<boolean> {
  if (!secret || !header || !header.startsWith('sha256=')) return false
  return safeEqual(header.slice('sha256='.length).toLowerCase(), await hmacHex(secret, body))
}

/** `Authorization: Bearer <password>` check for the read API. Empty passwords never match. */
export function checkBearer(password: string | undefined, header: string | null): boolean {
  if (!password || !header || !header.startsWith('Bearer ')) return false
  return safeEqual(header.slice('Bearer '.length), password)
}
