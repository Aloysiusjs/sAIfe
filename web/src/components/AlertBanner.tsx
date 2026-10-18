import { LoaderCircle, OctagonX } from 'lucide-react'
import type { Alert } from '../api/types'
import type { RunState } from '../lib/runState'
import { ANSWER_STATUS, SEVERITY } from '../lib/status'

interface Props {
  state: RunState
  onSelectClaim: (id: string) => void
}

function progress(state: RunState) {
  const pass = state.passes[state.passes.length - 1]
  if (!pass) return 'Generating the draft…'
  const total = pass.claimOrder.length
  const done = pass.claimOrder.filter((id) => state.passes.slice(0, pass.index + 1).some((p) => id in p.results)).length
  if (total === 0) return `Pass ${pass.index + 1}: extracting claims…`
  return `Pass ${pass.index + 1}: ${done} of ${total} claims resolved`
}

export function AlertBanner({ state, onSelectClaim }: Props) {
  if (state.phase === 'failed') {
    return (
      <div role="alert" className="rounded-lg border border-contradicted-line bg-contradicted-bg px-4 py-3 sm:px-5">
        <p className="flex items-center gap-2 font-semibold text-contradicted-fg">
          <OctagonX aria-hidden="true" className="h-5 w-5" />
          Run failed
        </p>
        <p className="mt-1 text-sm text-ink-900">
          {state.error ?? 'The run stopped before it finished.'} Nothing from this run has been verified.
        </p>
      </div>
    )
  }

  const status = state.report?.status ?? state.status
  if (!status) {
    return (
      <div aria-live="polite" className="flex items-center gap-3 rounded-lg border border-ink-200 bg-white px-4 py-3 shadow-panel sm:px-5">
        <LoaderCircle aria-hidden="true" className="h-5 w-5 shrink-0 text-action motion-safe:animate-spin" />
        <div>
          <p className="font-semibold text-ink-900">Checking the answer</p>
          <p className="text-sm text-ink-600">{progress(state)}. Nothing is verified until the run completes.</p>
        </div>
        {state.alerts.length > 0 ? (
          <span className="ml-auto text-xs text-ink-500">
            {state.alerts.length} alert{state.alerts.length === 1 ? '' : 's'} so far
          </span>
        ) : null}
      </div>
    )
  }

  const meta = ANSWER_STATUS[status]
  const Icon = meta.icon
  const alerts = state.report?.alerts ?? state.alerts
  return (
    <div role="status" aria-live="polite" className={`rounded-lg border ${meta.border} ${meta.tint} px-4 py-3 sm:px-5`}>
      <div className="flex items-start gap-3">
        <Icon aria-hidden="true" className={`mt-0.5 h-6 w-6 shrink-0 ${meta.text}`} strokeWidth={2} />
        <div className="min-w-0">
          <p className="text-base font-semibold text-ink-900">
            <span className={`mr-2 uppercase tracking-wide ${meta.text}`}>{meta.label}</span>
            <span className="font-normal text-ink-800">{meta.headline}</span>
          </p>
          {alerts.length > 0 ? (
            <ul className="mt-2 space-y-1.5">
              {alerts.map((a, i) => (
                <AlertRow key={i} alert={a} onSelectClaim={onSelectClaim} />
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function AlertRow({ alert, onSelectClaim }: { alert: Alert; onSelectClaim: (id: string) => void }) {
  const sev = SEVERITY[alert.severity]
  const Icon = sev.icon
  return (
    <li className="flex gap-2 text-sm text-ink-900">
      <Icon aria-hidden="true" className={`mt-0.5 h-4 w-4 shrink-0 ${sev.text}`} />
      <p className="min-w-0">
        <span className={`mr-1.5 font-semibold ${sev.text}`}>{sev.label}:</span>
        {alert.message}
        {alert.claim_ids.length > 0 ? (
          <>
            {' '}
            {alert.claim_ids.map((id, i) => (
              <button
                key={id}
                type="button"
                onClick={() => onSelectClaim(id)}
                className="ml-1 whitespace-nowrap rounded-sm font-medium text-action-fg underline decoration-1 underline-offset-2 hover:text-action-hover"
              >
                {alert.claim_ids.length > 1 ? `View claim ${i + 1}` : 'View claim'}
              </button>
            ))}
          </>
        ) : null}
      </p>
    </li>
  )
}
