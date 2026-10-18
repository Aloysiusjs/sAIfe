import { Eraser, PenLine, Wrench, MessageSquareWarning } from 'lucide-react'
import type { AlertView, RepairView } from '../lib/report'
import { REPAIR_OP, SEVERITY } from '../lib/status'
import { EvidenceQuote } from './ClaimCard'

const OP_ICON = { correct: Wrench, hedge: MessageSquareWarning, remove: Eraser } as const

export function RepairList({ repairs }: { repairs: RepairView[] }) {
  return (
    <ul className="space-y-3">
      {repairs.map((r, i) => {
        const meta = REPAIR_OP[r.op] ?? { label: r.op, verb: '' }
        const Icon = r.op in OP_ICON ? OP_ICON[r.op as keyof typeof OP_ICON] : PenLine
        return (
          <li key={`${r.claimId}-${i}`} className="panel p-3 sm:p-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="inline-flex items-center gap-1 rounded bg-ink-100 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-ink-700 dark:bg-ink-800 dark:text-ink-200">
                <Icon aria-hidden="true" className="h-3 w-3" />
                {meta.label}
              </span>
              <span className="text-xs text-ink-500 dark:text-ink-400">{meta.verb}</span>
              {r.passIndex !== null ? <span className="text-xs text-ink-500 dark:text-ink-400">pass {r.passIndex + 1}</span> : null}
            </div>
            <div className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
              <div className="min-w-0 rounded-md bg-contradicted-bg/60 p-2 dark:bg-contradicted-line/15">
                <p className="label-xs mb-1">Before</p>
                <del className="whitespace-pre-wrap break-words font-serif text-ink-700 decoration-contradicted-line dark:text-ink-300">{r.before}</del>
              </div>
              <div className="min-w-0 rounded-md bg-supported-bg/60 p-2 dark:bg-supported-line/15">
                <p className="label-xs mb-1">After</p>
                {r.after ? (
                  <ins className="whitespace-pre-wrap break-words font-serif no-underline">{r.after}</ins>
                ) : (
                  <span className="italic text-ink-500 dark:text-ink-400">(removed)</span>
                )}
              </div>
            </div>
            {r.reason ? <p className="mt-2 break-words text-sm text-ink-600 dark:text-ink-300">{r.reason}</p> : null}
            {r.evidence.length ? (
              <ul className="mt-2 space-y-1.5">
                {r.evidence.map((e, j) => (
                  <EvidenceQuote key={j} e={e} />
                ))}
              </ul>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

export function AlertList({ alerts }: { alerts: AlertView[] }) {
  return (
    <ul className="space-y-2">
      {alerts.map((a, i) => {
        const s = SEVERITY[a.severity]
        const Icon = s.icon
        return (
          <li key={i} className="panel flex gap-2.5 p-3 text-sm">
            <Icon aria-hidden="true" className={`mt-0.5 h-4 w-4 shrink-0 ${s.text}`} />
            <div className="min-w-0">
              <span className={`mr-2 text-2xs font-semibold uppercase tracking-wide ${s.text}`}>{s.label}</span>
              <span className="break-words">{a.message}</span>
              {a.claimIds.length ? (
                <p className="mt-0.5 break-all font-mono text-xs text-ink-500 dark:text-ink-400">{a.claimIds.join(', ')}</p>
              ) : null}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
