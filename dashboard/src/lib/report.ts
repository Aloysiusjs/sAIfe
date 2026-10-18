/**
 * Defensive view models for a run. The report and events are untrusted JSON, so every
 * field is read through a type guard and anything malformed is dropped or defaulted.
 */

import type { ClaimStatus, SaifeEvent, Severity } from '../../api/_lib/core'
import { CLAIM_STATUS_ORDER, isClaimStatus, isSeverity } from './status'

export interface EvidenceView {
  sourceId: string
  quote: string
  verified: boolean
  url: string | null
  method: string
}

export interface VerdictView {
  detector: string
  verdict: string
  confidence: number | null
  rationale: string | null
  error: string | null
  model: string | null
  fallback: boolean
  evidence: EvidenceView[]
}

export interface ClaimView {
  id: string
  text: string
  kind: string | null
  checkworthy: boolean
  /** null while the claim is extracted but not yet judged (live view). */
  status: ClaimStatus | null
  risk: number | null
  verdicts: VerdictView[]
}

export interface RepairView {
  claimId: string
  op: string
  before: string
  after: string
  reason: string
  passIndex: number | null
  evidence: EvidenceView[]
}

export interface AlertView {
  severity: Severity
  message: string
  claimIds: string[]
}

export interface FallbackView {
  role: string
  from: string
  to: string
  reason: string
}

export interface RunView {
  question: string | null
  finalAnswer: string | null
  originalDraft: string | null
  claims: ClaimView[]
  repairs: RepairView[]
  alerts: AlertView[]
  fallbacks: FallbackView[]
  inputTokens: number | null
  outputTokens: number | null
  calls: number | null
  byModel: string[]
  passes: number
  live: boolean
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

function evidence(v: unknown): EvidenceView[] {
  return arr(v)
    .filter(isObj)
    .map((e) => ({
      sourceId: str(e.source_id) ?? '',
      quote: str(e.quote) ?? '',
      verified: e.verified === true,
      url: str(e.url),
      method: str(e.method) ?? 'quote',
    }))
}

function verdicts(v: unknown): VerdictView[] {
  return arr(v)
    .filter(isObj)
    .map((d) => ({
      detector: str(d.detector) ?? 'unknown',
      verdict: str(d.verdict) ?? 'UNVERIFIABLE',
      confidence: num(d.confidence),
      rationale: str(d.rationale),
      error: str(d.error),
      model: str(d.model),
      fallback: d.fallback === true,
      evidence: evidence(d.evidence),
    }))
}

function claimResult(v: unknown): ClaimView | null {
  if (!isObj(v)) return null
  const c = isObj(v.claim) ? v.claim : null
  const id = c ? str(c.id) : null
  if (!c || !id) return null
  return {
    id,
    text: str(c.text) ?? '',
    kind: str(c.kind),
    checkworthy: c.checkworthy !== false,
    status: isClaimStatus(v.status) ? v.status : 'UNVERIFIABLE',
    risk: num(v.risk),
    verdicts: verdicts(v.verdicts),
  }
}

function repair(v: unknown): RepairView | null {
  if (!isObj(v)) return null
  return {
    claimId: str(v.claim_id) ?? '',
    op: str(v.op) ?? 'correct',
    before: str(v.before) ?? '',
    after: str(v.after) ?? '',
    reason: str(v.reason) ?? '',
    passIndex: num(v.pass_index),
    evidence: evidence(v.evidence),
  }
}

function alert(v: unknown): AlertView | null {
  if (!isObj(v) || !isSeverity(v.severity)) return null
  return {
    severity: v.severity,
    message: str(v.message) ?? '',
    claimIds: arr(v.claim_ids).filter((x): x is string => typeof x === 'string'),
  }
}

const notNull = <T>(x: T | null): x is T => x !== null

const SEVERITY_RANK: Record<Severity, number> = { high: 0, warning: 1, info: 2 }

/** Flagged first, then by risk (highest first); unjudged claims last. */
export function sortClaims(claims: ClaimView[]): ClaimView[] {
  const rank = (s: ClaimStatus | null) => (s === null ? CLAIM_STATUS_ORDER.length : CLAIM_STATUS_ORDER.indexOf(s))
  return [...claims].sort((a, b) => rank(a.status) - rank(b.status) || (b.risk ?? 0) - (a.risk ?? 0))
}

export function viewFromReport(report: Record<string, unknown>): RunView {
  const usage = isObj(report.usage) ? report.usage : {}
  return {
    question: str(report.question),
    finalAnswer: str(report.final_answer),
    originalDraft: str(report.original_draft),
    claims: sortClaims(arr(report.claims).map(claimResult).filter(notNull)),
    repairs: arr(report.repairs).map(repair).filter(notNull),
    alerts: arr(report.alerts)
      .map(alert)
      .filter(notNull)
      .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]),
    fallbacks: arr(report.fallbacks)
      .filter(isObj)
      .map((f) => ({
        role: str(f.role) ?? '',
        from: str(f.from_model) ?? '',
        to: str(f.to_model) ?? '',
        reason: str(f.reason) ?? '',
      })),
    inputTokens: num(usage.input_tokens),
    outputTokens: num(usage.output_tokens),
    calls: num(usage.calls),
    byModel: isObj(usage.by_model) ? Object.keys(usage.by_model).sort() : [],
    passes: arr(report.passes).length,
    live: false,
  }
}

/** Live view while a run is in flight: fold the stored events in sequence order. */
export function viewFromEvents(events: SaifeEvent[]): RunView {
  const claims = new Map<string, ClaimView>()
  const repairs: RepairView[] = []
  const alerts: AlertView[] = []
  let question: string | null = null
  let firstDraft: string | null = null
  let latestDraft: string | null = null
  let passes = 0
  for (const ev of [...events].sort((a, b) => a.seq - b.seq)) {
    const d = isObj(ev.data) ? ev.data : {}
    switch (ev.type) {
      case 'run.started':
        question = str(d.question)
        break
      case 'pass.started': {
        const draft = str(d.draft)
        if (draft !== null) {
          if (firstDraft === null) firstDraft = draft
          latestDraft = draft
        }
        passes = Math.max(passes, (num(d.index) ?? 0) + 1)
        break
      }
      case 'claim.extracted': {
        const c = isObj(d.claim) ? d.claim : null
        const id = c ? str(c.id) : null
        if (c && id && !claims.has(id)) {
          claims.set(id, {
            id,
            text: str(c.text) ?? '',
            kind: str(c.kind),
            checkworthy: c.checkworthy !== false,
            status: null,
            risk: null,
            verdicts: [],
          })
        }
        break
      }
      case 'claim.verdict': {
        const r = claimResult(d.result)
        if (r) claims.set(r.id, r)
        break
      }
      case 'repair.applied': {
        const r = repair(d.repair)
        if (r) repairs.push(r)
        const draft = str(d.draft)
        if (draft !== null) latestDraft = draft
        break
      }
      case 'alert.raised': {
        const a = alert(d.alert)
        if (a) alerts.push(a)
        break
      }
      default:
        break
    }
  }
  return {
    question,
    finalAnswer: latestDraft,
    originalDraft: firstDraft,
    claims: sortClaims([...claims.values()]),
    repairs,
    alerts,
    fallbacks: [],
    inputTokens: null,
    outputTokens: null,
    calls: null,
    byModel: [],
    passes,
    live: true,
  }
}

export const isFlagged = (s: ClaimStatus | null): boolean => s === 'CONTRADICTED' || s === 'UNSUPPORTED'
