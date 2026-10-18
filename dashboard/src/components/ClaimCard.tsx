import { CircleCheck, CircleSlash, ExternalLink, Shuffle } from 'lucide-react'
import type { ClaimView, EvidenceView, VerdictView } from '../lib/report'
import { CLAIM_STATUS, DETECTOR, VERDICT } from '../lib/status'
import { fmtPct, hostOf, safeHref, shortModel } from '../lib/format'
import { StatusBadge } from './StatusBadge'

export function EvidenceQuote({ e }: { e: EvidenceView }) {
  const url = safeHref(e.url)
  return (
    <li className="rounded-md border border-ink-100 bg-ink-50 px-2.5 py-2 text-sm dark:border-ink-800 dark:bg-ink-950">
      <blockquote className="whitespace-pre-wrap break-words font-serif text-ink-800 dark:text-ink-100">
        {e.method === 'computed' ? e.quote : `“${e.quote}”`}
      </blockquote>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-500 dark:text-ink-400">
        {e.verified ? (
          <span className="inline-flex items-center gap-1 font-medium text-supported-fg dark:text-supported-bg">
            <CircleCheck aria-hidden="true" className="h-3.5 w-3.5" />
            Verified by code
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 font-medium text-unverifiable-fg dark:text-ink-300">
            <CircleSlash aria-hidden="true" className="h-3.5 w-3.5" />
            Not verified
          </span>
        )}
        <span className="break-all font-mono">{e.sourceId || 'unknown source'}</span>
        {e.method === 'computed' ? <span>computed check</span> : null}
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-w-0 items-center gap-1 text-action underline-offset-2 hover:underline dark:text-[#8fb3f0]"
          >
            <ExternalLink aria-hidden="true" className="h-3 w-3 shrink-0" />
            <span className="truncate">{hostOf(url)}</span>
          </a>
        ) : null}
      </div>
    </li>
  )
}

function VerdictRow({ v }: { v: VerdictView }) {
  const meta = VERDICT[v.verdict]
  const status = meta?.status ?? 'UNVERIFIABLE'
  const s = CLAIM_STATUS[status]
  const Icon = s.icon
  return (
    <li className="py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="font-medium">{DETECTOR[v.detector] ?? v.detector}</span>
        <span className={`inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide ${s.text}`}>
          <Icon aria-hidden="true" className="h-3.5 w-3.5" />
          {meta?.label ?? v.verdict}
        </span>
        {v.confidence !== null ? <span className="text-xs tabular-nums text-ink-500 dark:text-ink-400">confidence {fmtPct(v.confidence)}</span> : null}
        <span className="font-mono text-xs text-ink-500 dark:text-ink-400" title={v.model ?? undefined}>
          {v.model ? shortModel(v.model) : 'code check'}
        </span>
        {v.fallback ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-likely-fg dark:text-likely-bg">
            <Shuffle aria-hidden="true" className="h-3.5 w-3.5" />
            fallback judge
          </span>
        ) : null}
      </div>
      {v.rationale ? <p className="mt-1 whitespace-pre-wrap break-words text-sm text-ink-600 dark:text-ink-300">{v.rationale}</p> : null}
      {v.error ? (
        <p className="mt-1 break-words text-sm text-contradicted-fg dark:text-contradicted-bg">
          <span className="font-semibold">Error:</span> {v.error}
        </p>
      ) : null}
      {v.evidence.length ? (
        <ul className="mt-2 space-y-1.5">
          {v.evidence.map((e, i) => (
            <EvidenceQuote key={i} e={e} />
          ))}
        </ul>
      ) : null}
    </li>
  )
}

export function ClaimCard({ c }: { c: ClaimView }) {
  const border = c.status ? CLAIM_STATUS[c.status].border : 'border-ink-300 dark:border-ink-600'
  return (
    <li className={`panel border-l-4 ${border} p-3 sm:p-4`}>
      <div className="flex flex-wrap items-start gap-2">
        <StatusBadge status={c.status} size="md" />
        <p className="min-w-0 flex-1 basis-60 whitespace-pre-wrap break-words font-serif text-[0.95rem] leading-relaxed">{c.text}</p>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 text-xs text-ink-500 dark:text-ink-400">
        {c.kind ? <span>{c.kind.replace('_', ' ')}</span> : null}
        {!c.checkworthy ? <span>not check-worthy (opinion or advice)</span> : null}
        {c.risk !== null ? <span className="tabular-nums">risk {fmtPct(c.risk)}</span> : null}
        <span className="font-mono">{c.id}</span>
      </div>
      {c.verdicts.length ? (
        <ul className="mt-2 divide-y divide-ink-100 border-t border-ink-100 dark:divide-ink-800 dark:border-ink-800">
          {c.verdicts.map((v, i) => (
            <VerdictRow key={`${v.detector}-${i}`} v={v} />
          ))}
        </ul>
      ) : c.status === null ? (
        <p className="mt-2 text-sm text-ink-500 dark:text-ink-400">Waiting for the judges…</p>
      ) : (
        <p className="mt-2 text-sm text-ink-500 dark:text-ink-400">No detector ran on this claim.</p>
      )}
    </li>
  )
}
