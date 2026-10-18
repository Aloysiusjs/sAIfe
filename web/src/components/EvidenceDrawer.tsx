import { X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { RunState } from '../lib/runState'
import { bestResult, resultAt } from '../lib/runState'
import { REPAIR_OP } from '../lib/status'
import { EvidenceItem, VerdictCard } from './EvidenceItem'
import { StatusBadge } from './StatusBadge'

interface Props {
  state: RunState
  selection: { claimId: string; passIndex: number } | null
  onClose: () => void
}

/**
 * Non-modal side panel (bottom sheet on phones). Focus moves in on open and back
 * to the claim on close; Escape closes it.
 */
export function EvidenceDrawer({ state, selection, onClose }: Props) {
  const panel = useRef<HTMLElement>(null)
  const returnTo = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!selection) return
    if (!returnTo.current) returnTo.current = document.activeElement as HTMLElement | null
    panel.current?.focus()
  }, [selection])

  useEffect(() => {
    if (!selection) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const close = () => {
    onClose()
    const el = returnTo.current
    returnTo.current = null
    if (el && document.contains(el)) el.focus()
  }

  if (!selection) return null
  const { claimId, passIndex } = selection
  const got = resultAt(state.passes, claimId, passIndex) ?? bestResult(state, claimId)
  const claim = got?.result.claim ?? state.passes[passIndex]?.claims[claimId]
  if (!claim) return null
  const result = got?.result ?? null
  const repairs = (state.report?.repairs ?? state.passes.flatMap((p) => p.repairs)).filter((r) => r.claim_id === claimId)

  return (
    <aside
      ref={panel}
      tabIndex={-1}
      aria-labelledby="drawer-title"
      className="fixed inset-x-0 bottom-0 z-30 flex max-h-[85vh] flex-col rounded-t-xl border border-ink-200 bg-canvas shadow-drawer outline-none md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[30rem] md:rounded-none md:border-y-0 md:border-r-0"
    >
      <header className="flex items-start gap-3 border-b border-ink-200 bg-white px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={result?.status ?? null} size="md" />
            <span className="text-xs text-ink-500">
              {claim.kind.replace('_', ' ')} claim · pass {(got?.fromPass ?? passIndex) + 1}
              {result ? ` · risk ${Math.round(result.risk * 100)}%` : ''}
            </span>
          </div>
          <h2 id="drawer-title" className="mt-2 text-base font-semibold leading-snug text-ink-900">
            {claim.text}
          </h2>
        </div>
        <button
          type="button"
          onClick={close}
          className="rounded-md p-1.5 text-ink-600 hover:bg-ink-100 hover:text-ink-900"
          aria-label="Close evidence"
        >
          <X aria-hidden="true" className="h-5 w-5" />
        </button>
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
        <div>
          <h3 className="text-xs font-semibold text-ink-700">Text in the answer</h3>
          <p className="mt-1 rounded-md bg-white px-3 py-2 text-sm text-ink-900 ring-1 ring-ink-200">{claim.anchor}</p>
        </div>

        {repairs.length > 0 ? (
          <div>
            <h3 className="text-xs font-semibold text-ink-700">What sAIfe changed</h3>
            <ul className="mt-1 space-y-2">
              {repairs.map((r, i) => (
                <li key={i} className="rounded-md bg-white px-3 py-2 text-sm ring-1 ring-ink-200">
                  <p className="font-semibold text-ink-900">
                    {REPAIR_OP[r.op].label} in pass {r.pass_index + 1}
                  </p>
                  <p className="mt-0.5 text-ink-800">{r.reason}</p>
                  {r.evidence.map((e, j) => (
                    <div key={j} className="mt-2">
                      <EvidenceItem evidence={e} sources={state.sources} />
                    </div>
                  ))}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div>
          <h3 className="text-xs font-semibold text-ink-700">Detector verdicts</h3>
          {result && result.verdicts.length > 0 ? (
            <ul className="mt-1 space-y-2">
              {result.verdicts.map((v) => (
                <VerdictCard key={v.detector} verdict={v} sources={state.sources} />
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm text-ink-600">
              {result ? 'No detector applied to this claim, so it was not verified.' : 'Still being checked…'}
            </p>
          )}
        </div>
      </div>
    </aside>
  )
}
