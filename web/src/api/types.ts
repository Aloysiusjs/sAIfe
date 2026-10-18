// Hand-written mirrors of src/saife/types.py and src/saife/config.py.
// Keep in step with the pydantic models: these are the UI's data contract.

export type ClaimKind = 'entity' | 'numeric' | 'date' | 'citation' | 'causal' | 'code_api' | 'opinion'
export type Verdict = 'SUPPORTED' | 'CONTRADICTED' | 'NOT_FOUND' | 'UNVERIFIABLE'
export type ClaimStatus = 'SUPPORTED' | 'CONTRADICTED' | 'LIKELY' | 'UNSUPPORTED' | 'UNVERIFIABLE'
export type AnswerStatus = 'VERIFIED' | 'CAUTION' | 'BLOCKED'
export type DetectorName = 'grounding' | 'consistency' | 'web_evidence' | 'deterministic'
export type SupportKind = 'evidence' | 'computed' | 'consistency'
export type RepairOp = 'correct' | 'hedge' | 'remove'
export type Severity = 'info' | 'warning' | 'high'
export type StopCause =
  | 'resolved'
  | 'max_passes'
  | 'token_budget'
  | 'timeout'
  | 'not_converging'
  | 'repair_disabled'
  | 'repair_failed'
  | 'decompose_failed'
export type PresetName = 'fast' | 'balanced' | 'strict'
export type Span = [number, number]

export interface Source {
  id: string
  text: string
  title?: string | null
  url?: string | null
}

export interface Claim {
  id: string
  text: string
  anchor: string
  span: Span | null
  kind: ClaimKind
  checkworthy: boolean
}

export interface Evidence {
  source_id: string
  quote: string
  url?: string | null
  method: 'quote' | 'computed'
  /** Set only by saife.evidence (code). Never by a model. */
  verified: boolean
}

export interface DetectorVerdict {
  detector: DetectorName
  claim_id: string
  verdict: Verdict
  confidence: number
  support_kind: SupportKind
  evidence: Evidence[]
  rationale?: string | null
  error?: string | null
  model?: string | null
  fallback: boolean
}

export interface ClaimResult {
  claim: Claim
  status: ClaimStatus
  verdicts: DetectorVerdict[]
  risk: number
  pass_index: number
}

export interface RepairRecord {
  claim_id: string
  op: RepairOp
  before: string
  after: string
  span: Span
  reason: string
  evidence: Evidence[]
  pass_index: number
}

export interface Alert {
  severity: Severity
  claim_ids: string[]
  message: string
}

export interface FallbackRecord {
  role: string
  from_model: string
  to_model: string
  reason: string
}

export interface PassRecord {
  index: number
  draft: string
  claim_ids: string[]
  verified_ids: string[]
  flagged_ids: string[]
  repairs: RepairRecord[]
  duration_s: number
}

export interface Usage {
  input_tokens: number
  output_tokens: number
}

export interface RunUsage {
  input_tokens: number
  output_tokens: number
  calls: number
  cost_usd: number
  wall_time_s: number
  by_model: Record<string, Usage>
}

export interface Report {
  run_id: string
  status: AnswerStatus
  preset: string
  question?: string | null
  original_draft: string
  final_answer: string
  claims: ClaimResult[]
  passes: PassRecord[]
  repairs: RepairRecord[]
  alerts: Alert[]
  usage: RunUsage
  risk: number
  stop_cause: StopCause
  verifier_diversity: boolean
  fallbacks: FallbackRecord[]
  trace_path?: string | null
}

// ---- Events (payload shapes from the Event docstring) ----------------------------

interface EventBase<T extends string, D> {
  type: T
  run_id: string
  seq: number
  ts: number
  data: D
}

export type RunStartedEvent = EventBase<
  'run.started',
  { question: string | null; preset: string; mode: 'generate' | 'answer'; sources: string[] }
>
export type PassStartedEvent = EventBase<'pass.started', { index: number; draft: string }>
export type ClaimExtractedEvent = EventBase<'claim.extracted', { pass_index: number; claim: Claim }>
export type ClaimVerdictEvent = EventBase<'claim.verdict', { pass_index: number; result: ClaimResult }>
export type RepairAppliedEvent = EventBase<
  'repair.applied',
  { pass_index: number; repair: RepairRecord; draft: string }
>
export type AlertRaisedEvent = EventBase<'alert.raised', { alert: Alert }>
export type RunCompletedEvent = EventBase<'run.completed', { status: AnswerStatus; report: Report }>
export type RunFailedEvent = EventBase<'run.failed', { error: string }>

export type RunEvent =
  | RunStartedEvent
  | PassStartedEvent
  | ClaimExtractedEvent
  | ClaimVerdictEvent
  | RepairAppliedEvent
  | AlertRaisedEvent
  | RunCompletedEvent
  | RunFailedEvent

export type EventType = RunEvent['type']

export const EVENT_TYPES: readonly EventType[] = [
  'run.started',
  'pass.started',
  'claim.extracted',
  'claim.verdict',
  'repair.applied',
  'alert.raised',
  'run.completed',
  'run.failed',
]

// ---- Config (GET /api/presets) ---------------------------------------------------

export interface Budgets {
  max_passes: number
  max_tokens: number
  timeout_s: number
}

export interface GuardConfig {
  preset: string
  detectors: DetectorName[]
  budgets: Budgets
  repair: boolean
  consistency_samples: number
  web_max_uses: number
  repair_statuses: string[]
  block_on_contradicted: boolean
  block_risk_threshold: number
  fallback_policy: 'fallback_and_disclose' | 'fail_closed'
  roles?: Record<string, { provider: string; model: string }> | null
}

// ---- Requests --------------------------------------------------------------------

export interface SourceInput {
  id: string
  text: string
  title?: string
}

export interface RunRequest {
  question?: string
  answer?: string
  sources?: SourceInput[]
  preset: PresetName
}

export interface Trap {
  id: string
  category: string
  question: string
  sources?: SourceInput[] | null
  notes: string
}
