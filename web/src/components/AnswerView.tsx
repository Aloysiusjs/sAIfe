import type { Claim, ClaimStatus } from '../api/types'
import type { RunState } from '../lib/runState'
import { bestResult, resultAt } from '../lib/runState'
import { segmentText } from '../lib/spans'
import { CLAIM_STATUS, CLAIM_STATUS_ORDER, PENDING_META } from '../lib/status'
import { activateOnKey, onClaimArrowKeys } from './keyboard'
import { Section } from './Section'
import { StatusBadge } from './StatusBadge'

interface Props {
  state: RunState
  selectedId: string | null
  onSelect: (claimId: string, passIndex: number) => void
}

export function AnswerView({ state, selectedId, onSelect }: Props) {
  const last = state.passes[state.passes.length - 1]
  const report = state.report
  if (!last && !report) return null

  const passIndex = last?.index ?? 0
  const claims: Claim[] = last ? last.claimOrder.map((id) => last.claims[id]).filter((c): c is Claim => !!c) : []
  const statusOf = (id: string): ClaimStatus | null =>
    (report ? bestResult(state, id) : resultAt(state.passes, id, passIndex))?.result.status ?? null

  const blocked = report?.status === 'BLOCKED'
  const title = report ? (blocked ? 'Answer withheld' : 'Final answer') : `Draft under review · pass ${passIndex + 1}`
  const text = report && !blocked ? report.final_answer : (last?.draft ?? report?.final_answer ?? '')

  const counts = new Map<ClaimStatus, number>()
  for (const c of claims) {
    const s = statusOf(c.id)
    if (s) counts.set(s, (counts.get(s) ?? 0) + 1)
  }

  const body = (
    <HighlightedText text={text} claims={claims} statusOf={statusOf} selectedId={selectedId} onSelect={(id) => onSelect(id, passIndex)} />
  )

  return (
    <Section
      id="answer-title"
      title={title}
      aside={
        claims.length ? (
          <span className="flex flex-wrap gap-1.5">
            {CLAIM_STATUS_ORDER.filter((s) => counts.get(s)).map((s) => (
              <span key={s} className="inline-flex items-center gap-1">
                <StatusBadge status={s} />
                <span className="tabular-nums text-ink-600">{counts.get(s)}</span>
              </span>
            ))}
          </span>
        ) : null
      }
    >
      {blocked && report ? (
        <>
          <p className="max-w-[68ch] text-base leading-relaxed text-ink-900">{report.final_answer}</p>
          <details className="mt-4 rounded-md border border-ink-200 bg-ink-50 px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium text-ink-800">
              Show the checked draft (not safe to use)
            </summary>
            <div className="mt-3">{body}</div>
          </details>
        </>
      ) : (
        body
      )}
      <Legend />
    </Section>
  )
}

interface HighlightProps {
  text: string
  claims: Claim[]
  statusOf: (id: string) => ClaimStatus | null
  selectedId: string | null
  onSelect: (id: string) => void
}

function HighlightedText({ text, claims, statusOf, selectedId, onSelect }: HighlightProps) {
  const { segments, unplaced } = segmentText(text, claims)
  const byId = new Map(claims.map((c) => [c.id, c]))
  return (
    <div onKeyDown={onClaimArrowKeys}>
      <p className="max-w-[68ch] whitespace-pre-wrap text-[1.0625rem] leading-[1.9] text-ink-900">
        {segments.map((seg, i) => {
          if (!seg.claimId) return <span key={i}>{seg.text}</span>
          const claim = byId.get(seg.claimId)
          if (!claim) return <span key={i}>{seg.text}</span>
          return (
            <ClaimMark
              key={i}
              text={seg.text}
              claim={claim}
              status={statusOf(claim.id)}
              selected={selectedId === claim.id}
              onSelect={onSelect}
            />
          )
        })}
      </p>
      {unplaced.length > 0 ? (
        <div className="mt-4 border-t border-ink-100 pt-3">
          <h3 className="text-xs font-semibold text-ink-700">Checked claims that could not be located in the text</h3>
          <ul className="mt-2 space-y-1.5">
            {unplaced.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  data-claim-nav
                  onClick={() => onSelect(c.id)}
                  className="flex items-start gap-2 rounded text-left text-sm text-ink-900 hover:underline"
                >
                  <StatusBadge status={statusOf(c.id)} />
                  <span>{c.text}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

interface MarkProps {
  text: string
  claim: Claim
  status: ClaimStatus | null
  selected: boolean
  onSelect: (id: string) => void
}

function ClaimMark({ text, claim, status, selected, onSelect }: MarkProps) {
  const meta = status ? CLAIM_STATUS[status] : PENDING_META
  const Icon = meta.icon
  const open = () => onSelect(claim.id)
  return (
    <span
      role="button"
      tabIndex={0}
      data-claim-nav
      aria-label={`${meta.label}: ${claim.text}. Open evidence.`}
      aria-pressed={selected}
      onClick={open}
      onKeyDown={(e) => activateOnKey(e, open)}
      className={`claim-mark cursor-pointer rounded-sm px-0.5 underline decoration-2 underline-offset-[5px] transition-colors duration-200 ease-out hover:brightness-[0.97] ${meta.mark} ${selected ? 'outline outline-2 outline-offset-1 outline-ink-900' : ''}`}
    >
      <Icon
        aria-hidden="true"
        className={`mr-0.5 inline-block h-[0.95em] w-[0.95em] -translate-y-[0.1em] ${meta.text} ${status ? '' : 'motion-safe:animate-spin'}`}
        strokeWidth={2.25}
      />
      {text}
      <span aria-hidden="true" className={`ml-1 inline-block align-[0.12em] text-2xs font-semibold uppercase tracking-wide ${meta.text}`}>
        {meta.label}
      </span>
    </span>
  )
}

function Legend() {
  return (
    <details className="mt-5 border-t border-ink-100 pt-3 text-sm">
      <summary className="cursor-pointer text-xs font-medium text-ink-600">What the statuses mean</summary>
      <dl className="mt-3 grid gap-2 sm:grid-cols-2">
        {CLAIM_STATUS_ORDER.map((s) => (
          <div key={s} className="flex items-start gap-2">
            <dt>
              <StatusBadge status={s} />
            </dt>
            <dd className="text-xs leading-5 text-ink-700">{CLAIM_STATUS[s].meaning}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}
