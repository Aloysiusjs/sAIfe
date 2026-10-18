import { BadgeCheck, Calculator, ExternalLink, RefreshCw, ShieldQuestion, TriangleAlert } from 'lucide-react'
import type { DetectorVerdict, Evidence, Source } from '../api/types'
import { CLAIM_STATUS, DETECTOR, VERDICT } from '../lib/status'

function sourceLabel(e: Evidence, sources: Source[]) {
  const s = sources.find((x) => x.id === e.source_id)
  return { title: s?.title ?? e.source_id, url: e.url ?? s?.url ?? null }
}

export function EvidenceItem({ evidence, sources }: { evidence: Evidence; sources: Source[] }) {
  if (evidence.method === 'computed' && evidence.verified) {
    return (
      <div className="rounded-md border border-ink-200 bg-ink-50 px-3 py-2">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-ink-800">
          <Calculator aria-hidden="true" className="h-3.5 w-3.5" />
          Computed check · {evidence.source_id.replace(/^deterministic:/, '')}
        </p>
        <p className="mt-1 break-words font-mono text-xs leading-5 text-ink-900">{evidence.quote}</p>
      </div>
    )
  }
  const { title, url } = sourceLabel(evidence, sources)
  if (!evidence.verified) {
    return (
      <div className="rounded-md border-2 border-dashed border-unverifiable-line bg-white px-3 py-2">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-unverifiable-fg">
          <ShieldQuestion aria-hidden="true" className="h-3.5 w-3.5" />
          Evidence could not be confirmed
        </p>
        <p className="mt-1 text-xs text-ink-700">
          The judge offered this quote, but code did not find it in <span className="font-medium">{title}</span>. It is
          ignored and does not count as support.
        </p>
        <blockquote className="mt-1.5 font-serif text-sm italic text-ink-600 line-through decoration-ink-400">
          “{evidence.quote}”
        </blockquote>
      </div>
    )
  }
  return (
    <div className="rounded-md border border-supported-line/60 bg-white px-3 py-2">
      <p className="flex items-center gap-1.5 text-xs font-semibold text-supported-fg">
        <BadgeCheck aria-hidden="true" className="h-3.5 w-3.5" />
        Quote verified in source
      </p>
      <blockquote className="mt-1.5 font-serif text-[0.95rem] leading-relaxed text-ink-900">“{evidence.quote}”</blockquote>
      <p className="mt-1.5 text-xs text-ink-600">
        {url ? (
          <a href={url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 font-medium text-action-fg underline underline-offset-2 hover:text-action-hover">
            {title}
            <ExternalLink aria-hidden="true" className="h-3 w-3" />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        ) : (
          <span className="font-medium">{title}</span>
        )}
      </p>
    </div>
  )
}

export function VerdictCard({ verdict, sources }: { verdict: DetectorVerdict; sources: Source[] }) {
  const v = VERDICT[verdict.verdict]
  const meta = CLAIM_STATUS[v.status]
  const Icon = meta.icon
  return (
    <li className="rounded-lg border border-ink-200 bg-white p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold text-ink-900">{DETECTOR[verdict.detector].label}</h4>
        <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide ${meta.tint} ${meta.text}`}>
          <Icon aria-hidden="true" className="h-3 w-3" strokeWidth={2.25} />
          {v.label}
        </span>
        {verdict.error ? null : (
          <span className="text-xs tabular-nums text-ink-500">confidence {Math.round(verdict.confidence * 100)}%</span>
        )}
      </div>
      <p className="mt-0.5 text-xs text-ink-500">{DETECTOR[verdict.detector].blurb}</p>
      {verdict.support_kind === 'consistency' && verdict.verdict === 'SUPPORTED' ? (
        <p className="mt-1.5 text-xs text-likely-fg">Agreement only, no source evidence: this can raise a claim to Likely, never to Supported.</p>
      ) : null}
      {verdict.rationale ? <p className="mt-2 text-sm text-ink-800">{verdict.rationale}</p> : null}
      {verdict.error ? (
        <div className="mt-2 rounded-md border-2 border-dashed border-unverifiable-line px-3 py-2">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-unverifiable-fg">
            <TriangleAlert aria-hidden="true" className="h-3.5 w-3.5" />
            Evidence could not be confirmed: the check failed
          </p>
          <p className="mt-1 break-words font-mono text-xs text-ink-800">{verdict.error}</p>
          <p className="mt-1 text-xs text-ink-700">Counted as Unverifiable, never as support.</p>
        </div>
      ) : null}
      {verdict.evidence.length > 0 ? (
        <ul className="mt-2 space-y-2">
          {verdict.evidence.map((e, i) => (
            <li key={i}>
              <EvidenceItem evidence={e} sources={sources} />
            </li>
          ))}
        </ul>
      ) : null}
      {verdict.model || verdict.fallback ? (
        <p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-600">
          {verdict.model ? (
            <>
              <span>Judged by</span>
              <code className="rounded bg-ink-50 px-1 font-mono text-ink-800">{verdict.model}</code>
            </>
          ) : null}
          {verdict.fallback ? (
            <span
              className="inline-flex items-center gap-1 rounded border border-likely-line bg-likely-bg px-1.5 py-0.5 font-semibold text-likely-fg"
              title="The verifier was unavailable, so this check ran on the generator's provider and is not cross-provider."
            >
              <RefreshCw aria-hidden="true" className="h-3 w-3" />
              Fallback model
            </span>
          ) : null}
        </p>
      ) : null}
    </li>
  )
}
