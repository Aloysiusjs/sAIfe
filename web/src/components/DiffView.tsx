import { useState } from 'react'
import type { ClaimResult, RepairRecord, Report } from '../api/types'
import { diffPieces } from '../lib/spans'
import { REPAIR_OP } from '../lib/status'
import { onClaimArrowKeys } from './keyboard'
import { Section } from './Section'
import { StatusBadge } from './StatusBadge'

interface Props {
  report: Report
  onSelect: (claimId: string) => void
}

export function DiffView({ report, onSelect }: Props) {
  const [mode, setMode] = useState<'inline' | 'side'>('inline')
  const repairs = report.repairs
  const numberOf = (r: RepairRecord) => repairs.indexOf(r) + 1
  const lastPass = report.passes[report.passes.length - 1]
  const finalIds = new Set(lastPass?.claim_ids ?? [])
  const removed: ClaimResult[] = report.claims.filter((c) => !finalIds.has(c.claim.id))
  const passesWithEdits = report.passes.filter((p) => repairs.some((r) => r.pass_index === p.index))

  return (
    <Section
      id="diff-title"
      title="Changes from the original draft"
      aside={
        repairs.length > 0 ? (
          <div role="group" aria-label="Diff layout" className="flex rounded-md border border-ink-200 p-0.5">
            {(['inline', 'side'] as const).map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={mode === m}
                onClick={() => setMode(m)}
                className={`rounded px-2 py-0.5 text-xs font-medium ${mode === m ? 'bg-ink-900 text-white' : 'text-ink-700 hover:bg-ink-100'}`}
              >
                {m === 'inline' ? 'Inline edits' : 'Side by side'}
              </button>
            ))}
          </div>
        ) : null
      }
    >
      {repairs.length === 0 ? (
        <p className="text-sm text-ink-700">No edits. The final answer is the original draft.</p>
      ) : mode === 'side' ? (
        <div className="grid gap-4 md:grid-cols-2">
          <TextPane title="Original draft" text={report.original_draft} />
          <TextPane title={report.status === 'BLOCKED' ? 'Shown to the user' : 'Final answer'} text={report.final_answer} />
        </div>
      ) : (
        <div className="space-y-5">
          {passesWithEdits.map((p) => {
            const own = repairs.filter((r) => r.pass_index === p.index)
            const { pieces, unplaced } = diffPieces(p.draft, own, numberOf)
            return (
              <div key={p.index}>
                <h3 className="text-xs font-semibold text-ink-700">
                  Pass {p.index + 1} draft, with the edits made after it was checked
                </h3>
                <p className="mt-2 max-w-[68ch] whitespace-pre-wrap text-[0.95rem] leading-[1.9] text-ink-900">
                  {pieces.map((piece, i) =>
                    piece.kind === 'same' || !piece.repair ? (
                      <span key={i}>{piece.text}</span>
                    ) : (
                      <span key={i}>
                        <del className="bg-contradicted-bg text-contradicted-fg decoration-contradicted-line">
                          <span className="sr-only">Removed text: </span>
                          {piece.text}
                        </del>
                        {piece.repair.after ? (
                          <ins className="bg-supported-bg text-supported-fg no-underline">
                            <span className="sr-only">Added text: </span>
                            {piece.repair.after}
                          </ins>
                        ) : null}
                        <sup className="ml-0.5 font-semibold text-ink-700">[{piece.number}]</sup>
                      </span>
                    ),
                  )}
                </p>
                {unplaced.length > 0 ? (
                  <p className="mt-1 text-xs text-ink-500">
                    {unplaced.length} edit{unplaced.length === 1 ? '' : 's'} could not be placed in this draft; see the list below.
                  </p>
                ) : null}
              </div>
            )
          })}
          <ol className="space-y-2 border-t border-ink-100 pt-4" onKeyDown={onClaimArrowKeys}>
            {repairs.map((r) => (
              <li key={numberOf(r)} className="flex gap-3 text-sm">
                <span className="w-7 shrink-0 font-semibold tabular-nums text-ink-700">[{numberOf(r)}]</span>
                <div className="min-w-0">
                  <p>
                    <span className="mr-2 rounded bg-ink-100 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-ink-800">
                      {REPAIR_OP[r.op].label}
                    </span>
                    <span className="text-ink-900">{r.reason}</span>
                  </p>
                  <button
                    type="button"
                    data-claim-nav
                    onClick={() => onSelect(r.claim_id)}
                    className="mt-0.5 rounded-sm text-xs font-medium text-action-fg underline underline-offset-2 hover:text-action-hover"
                  >
                    View the evidence for this edit
                  </button>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {removed.length > 0 ? (
        <div className="mt-5 border-t border-ink-100 pt-4">
          <h3 className="text-xs font-semibold text-ink-700">Claims no longer in the answer</h3>
          <ul className="mt-2 space-y-1.5" onKeyDown={onClaimArrowKeys}>
            {removed.map((c) => (
              <li key={c.claim.id}>
                <button
                  type="button"
                  data-claim-nav
                  onClick={() => onSelect(c.claim.id)}
                  className="flex items-start gap-2 rounded text-left text-sm text-ink-900 hover:underline"
                >
                  <StatusBadge status={c.status} />
                  <span className="line-through decoration-ink-400">{c.claim.text}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Section>
  )
}

function TextPane({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-md border border-ink-200 bg-ink-50 p-3">
      <h3 className="text-xs font-semibold text-ink-700">{title}</h3>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink-900">{text}</p>
    </div>
  )
}
