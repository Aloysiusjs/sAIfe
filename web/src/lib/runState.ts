import type {
  Alert,
  AnswerStatus,
  Claim,
  ClaimResult,
  RepairRecord,
  Report,
  RunEvent,
  Source,
} from '../api/types'

export interface PassView {
  index: number
  draft: string
  claimOrder: string[]
  claims: Record<string, Claim>
  /** Results verified in this pass (delta). Claims not here were carried from an earlier pass. */
  results: Record<string, ClaimResult>
  repairs: RepairRecord[]
  flaggedIds?: string[]
  durationS?: number
}

export type Phase = 'idle' | 'connecting' | 'running' | 'completed' | 'failed'

export interface RunState {
  phase: Phase
  runId: string | null
  question: string | null
  preset: string | null
  mode: 'generate' | 'answer' | null
  sources: Source[]
  passes: PassView[]
  alerts: Alert[]
  status: AnswerStatus | null
  report: Report | null
  error: string | null
  startedTs: number | null
  lastTs: number | null
}

export const initialRunState: RunState = {
  phase: 'idle',
  runId: null,
  question: null,
  preset: null,
  mode: null,
  sources: [],
  passes: [],
  alerts: [],
  status: null,
  report: null,
  error: null,
  startedTs: null,
  lastTs: null,
}

export type RunAction =
  | { type: 'connect'; sources: Source[]; question: string | null; preset: string }
  | { type: 'event'; event: RunEvent }
  | { type: 'report'; report: Report }
  | { type: 'error'; message: string }
  | { type: 'reset' }

function emptyPass(index: number, draft = ''): PassView {
  return { index, draft, claimOrder: [], claims: {}, results: {}, repairs: [] }
}

/** Return a copy of `passes` with pass `index` present and replaced by `fn(pass)`. */
function withPass(passes: PassView[], index: number, fn: (p: PassView) => PassView): PassView[] {
  const next = passes.slice()
  while (next.length <= index) next.push(emptyPass(next.length))
  next[index] = fn(next[index] ?? emptyPass(index))
  return next
}

function addClaim(p: PassView, claim: Claim): PassView {
  const known = claim.id in p.claims
  return {
    ...p,
    claims: { ...p.claims, [claim.id]: claim },
    claimOrder: known ? p.claimOrder : [...p.claimOrder, claim.id],
  }
}

/** Fold a finished Report into the pass views, so the timeline is complete even if events were missed. */
function mergeReport(state: RunState, report: Report): RunState {
  const byId = new Map(report.claims.map((r) => [r.claim.id, r]))
  let passes = state.passes
  for (const rec of report.passes) {
    passes = withPass(passes, rec.index, (p) => {
      let next: PassView = { ...p, draft: p.draft || rec.draft, flaggedIds: rec.flagged_ids, durationS: rec.duration_s }
      for (const id of rec.claim_ids) {
        const r = byId.get(id)
        if (r && !(id in next.claims)) next = addClaim(next, r.claim)
      }
      const results = { ...next.results }
      for (const id of rec.verified_ids) {
        const r = byId.get(id)
        if (r && !(id in results) && r.pass_index === rec.index) results[id] = r
      }
      // A pass's own repairs are authoritative in the report.
      return { ...next, results, repairs: rec.repairs.length ? rec.repairs : next.repairs }
    })
  }
  return {
    ...state,
    passes,
    report,
    status: report.status,
    alerts: report.alerts.length ? report.alerts : state.alerts,
    question: state.question ?? report.question ?? null,
    preset: state.preset ?? report.preset,
    phase: 'completed',
  }
}

function applyEvent(state: RunState, e: RunEvent): RunState {
  const base: RunState = { ...state, runId: e.run_id, lastTs: e.ts, startedTs: state.startedTs ?? e.ts }
  switch (e.type) {
    case 'run.started':
      return {
        ...base,
        phase: 'running',
        question: e.data.question ?? base.question,
        preset: e.data.preset,
        mode: e.data.mode,
        startedTs: e.ts,
      }
    case 'pass.started':
      return {
        ...base,
        phase: 'running',
        passes: withPass(base.passes, e.data.index, (p) => ({ ...p, draft: e.data.draft })),
      }
    case 'claim.extracted':
      return { ...base, passes: withPass(base.passes, e.data.pass_index, (p) => addClaim(p, e.data.claim)) }
    case 'claim.verdict': {
      const r = e.data.result
      return {
        ...base,
        passes: withPass(base.passes, e.data.pass_index, (p) => {
          const withClaim = r.claim.id in p.claims ? p : addClaim(p, r.claim)
          return { ...withClaim, results: { ...withClaim.results, [r.claim.id]: r } }
        }),
      }
    }
    case 'repair.applied':
      return {
        ...base,
        passes: withPass(base.passes, e.data.pass_index, (p) => ({ ...p, repairs: [...p.repairs, e.data.repair] })),
      }
    case 'alert.raised':
      return { ...base, alerts: [...base.alerts, e.data.alert] }
    case 'run.completed':
      return e.data.report
        ? mergeReport({ ...base, status: e.data.status }, e.data.report)
        : { ...base, status: e.data.status }
    case 'run.failed':
      return { ...base, phase: 'failed', error: e.data.error }
  }
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case 'connect':
      return {
        ...initialRunState,
        phase: 'connecting',
        sources: action.sources,
        question: action.question,
        preset: action.preset,
      }
    case 'event':
      return applyEvent(state, action.event)
    case 'report':
      return mergeReport(state, action.report)
    case 'error':
      return { ...state, phase: 'failed', error: action.message }
    case 'reset':
      return initialRunState
  }
}

export interface ResolvedResult {
  result: ClaimResult
  /** Pass the result came from; earlier than the asked pass when the claim was carried over unchanged. */
  fromPass: number
}

/** Latest result for a claim at or before `passIndex` (claims keep their ID when unchanged). */
export function resultAt(passes: PassView[], id: string, passIndex: number): ResolvedResult | null {
  for (let i = Math.min(passIndex, passes.length - 1); i >= 0; i--) {
    const r = passes[i]?.results[id]
    if (r) return { result: r, fromPass: i }
  }
  return null
}

/** Best-known result for a claim: the report's, else the latest from events. */
export function bestResult(state: RunState, id: string): ResolvedResult | null {
  const fromReport = state.report?.claims.find((r) => r.claim.id === id)
  if (fromReport) return { result: fromReport, fromPass: fromReport.pass_index }
  return resultAt(state.passes, id, state.passes.length - 1)
}
