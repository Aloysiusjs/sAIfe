import { History } from 'lucide-react'
import type { RunState } from '../lib/runState'
import { resultAt } from '../lib/runState'
import { CLAIM_STATUS, PENDING_META, REPAIR_OP } from '../lib/status'
import { onClaimArrowKeys } from './keyboard'
import { Section } from './Section'

interface Props {
  state: RunState
  onSelect: (claimId: string, passIndex: number) => void
  selected: { claimId: string; passIndex: number } | null
}

function truncate(text: string, n: number) {
  return text.length > n ? `${text.slice(0, n - 1).trimEnd()}…` : text
}

export function PassTimeline({ state, onSelect, selected }: Props) {
  const running = state.phase === 'running' || state.phase === 'connecting'
  return (
    <Section
      id="timeline-title"
      title="Passes"
      aside={running ? 'Live' : `${state.passes.length} pass${state.passes.length === 1 ? '' : 'es'}`}
    >
      {state.passes.length === 0 ? (
        <p className="text-sm text-ink-600">{running ? 'Waiting for the first draft…' : 'No passes yet.'}</p>
      ) : (
        <ol className="space-y-5" onKeyDown={onClaimArrowKeys}>
          {state.passes.map((pass) => {
            const resolved = pass.claimOrder.filter((id) => resultAt(state.passes, id, pass.index)).length
            const flagged = pass.flaggedIds?.length
            return (
              <li key={pass.index} className="relative pl-6">
                <span aria-hidden="true" className="absolute left-0 top-1 h-4 w-4 rounded-full border-2 border-ink-700 bg-white" />
                {pass.index < state.passes.length - 1 ? (
                  <span aria-hidden="true" className="absolute bottom-[-1.25rem] left-[7px] top-6 w-0.5 bg-ink-200" />
                ) : null}
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <h3 className="text-sm font-semibold text-ink-900">Pass {pass.index + 1}</h3>
                  <p className="text-xs tabular-nums text-ink-500">
                    {pass.claimOrder.length} claims · {resolved} resolved
                    {flagged !== undefined ? ` · ${flagged} flagged` : ''}
                    {pass.durationS ? ` · ${pass.durationS.toFixed(1)} s` : ''}
                  </p>
                </div>
                {pass.claimOrder.length > 0 ? (
                  <ul className="mt-2 flex flex-wrap gap-1.5" aria-label={`Claims in pass ${pass.index + 1}`}>
                    {pass.claimOrder.map((id) => {
                      const claim = pass.claims[id]
                      if (!claim) return null
                      const got = resultAt(state.passes, id, pass.index)
                      const carried = got !== null && got.fromPass < pass.index
                      const meta = got ? CLAIM_STATUS[got.result.status] : PENDING_META
                      const Icon = meta.icon
                      const isSelected = selected?.claimId === id && selected.passIndex === pass.index
                      return (
                        <li key={id} className="max-w-full">
                          <button
                            type="button"
                            data-claim-nav
                            onClick={() => onSelect(id, pass.index)}
                            aria-pressed={isSelected}
                            title={claim.text}
                            className={`group flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-left text-xs transition-colors duration-200 ease-out ${meta.tint} ${meta.border} ${isSelected ? 'ring-2 ring-ink-900 ring-offset-1' : ''} ${carried ? 'border-dashed' : ''}`}
                          >
                            <Icon
                              aria-hidden="true"
                              className={`h-3.5 w-3.5 shrink-0 ${meta.text} ${got ? '' : 'motion-safe:animate-spin'}`}
                              strokeWidth={2.25}
                            />
                            <span className={`shrink-0 font-semibold uppercase tracking-wide ${meta.text}`}>{meta.label}</span>
                            <span className="truncate text-ink-900">{truncate(claim.text, 56)}</span>
                            {carried && got ? (
                              <span className="flex shrink-0 items-center gap-0.5 text-ink-600">
                                <History aria-hidden="true" className="h-3 w-3" />
                                <span>pass {got.fromPass + 1}</span>
                              </span>
                            ) : null}
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                ) : (
                  <p className="mt-2 text-xs text-ink-500">Extracting claims…</p>
                )}
                {pass.repairs.length > 0 ? (
                  <p className="mt-2 text-xs text-ink-600">
                    <span className="font-semibold text-ink-800">Repairs:</span>{' '}
                    {pass.repairs.map((r) => REPAIR_OP[r.op].label.toLowerCase()).join(', ')}
                    {pass.index < state.passes.length - 1 ? ` → pass ${pass.index + 2}` : ' → final answer'}
                  </p>
                ) : null}
              </li>
            )
          })}
        </ol>
      )}
      <p className="mt-4 text-2xs text-ink-500">
        Dashed chips were unchanged since an earlier pass and were not re-checked. Use the arrow keys to move between claims.
      </p>
    </Section>
  )
}
