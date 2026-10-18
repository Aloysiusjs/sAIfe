import type { AnswerStatus, ClaimStatus } from '../../api/_lib/core'
import { ANSWER_STATUS, CLAIM_STATUS, PENDING_META } from '../lib/status'

interface ClaimProps {
  status: ClaimStatus | null
  size?: 'sm' | 'md'
  className?: string
}

const pad = (size: 'sm' | 'md') => (size === 'sm' ? 'gap-1 px-1.5 py-0.5 text-2xs' : 'gap-1.5 px-2 py-1 text-xs')
const iconSize = (size: 'sm' | 'md') => (size === 'sm' ? 'h-3 w-3' : 'h-3.5 w-3.5')

/** Icon + label + colour. Never colour alone (N5). `null` means the claim is still being checked. */
export function StatusBadge({ status, size = 'sm', className = '' }: ClaimProps) {
  const meta = status ? CLAIM_STATUS[status] : PENDING_META
  const Icon = meta.icon
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded font-semibold uppercase tracking-wide ${pad(size)} ${meta.tint} ${meta.text} ${className}`}
    >
      <Icon aria-hidden="true" className={`${iconSize(size)} ${status ? '' : 'motion-safe:animate-spin'}`} strokeWidth={2.25} />
      {meta.label}
    </span>
  )
}

export function AnswerBadge({ status, size = 'sm' }: { status: AnswerStatus; size?: 'sm' | 'md' }) {
  const meta = ANSWER_STATUS[status]
  const Icon = meta.icon
  return (
    <span className={`inline-flex shrink-0 items-center rounded font-semibold uppercase tracking-wide ${pad(size)} ${meta.tint} ${meta.text}`}>
      <Icon aria-hidden="true" className={iconSize(size)} strokeWidth={2.25} />
      {meta.label}
    </span>
  )
}

/** Live pill for in-flight runs. The dot pulses only when motion is allowed. */
export function RunningPill({ size = 'sm' }: { size?: 'sm' | 'md' }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border border-action/40 bg-action-soft font-semibold uppercase tracking-wide text-action-fg dark:border-action/60 dark:bg-action/25 dark:text-ink-100 ${pad(size)}`}
    >
      <span className="relative inline-flex h-2 w-2" aria-hidden="true">
        <span className="absolute inline-flex h-full w-full rounded-full bg-action opacity-60 motion-safe:animate-ping dark:bg-[#8fb3f0]" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-action dark:bg-[#8fb3f0]" />
      </span>
      Running
    </span>
  )
}

export function FailedBadge({ size = 'sm' }: { size?: 'sm' | 'md' }) {
  const meta = CLAIM_STATUS.UNVERIFIABLE
  const Icon = meta.icon
  return (
    <span className={`inline-flex shrink-0 items-center rounded font-semibold uppercase tracking-wide ${pad(size)} ${meta.tint} ${meta.text}`}>
      <Icon aria-hidden="true" className={iconSize(size)} strokeWidth={2.25} />
      Failed
    </span>
  )
}

/** The right badge for a run summary: live, failed, the answer status, or "no status". */
export function RunStateBadge({
  state,
  status,
  size = 'sm',
}: {
  state: string
  status: AnswerStatus | null
  size?: 'sm' | 'md'
}) {
  if (state === 'running') return <RunningPill size={size} />
  if (state === 'failed') return <FailedBadge size={size} />
  if (status) return <AnswerBadge status={status} size={size} />
  return <span className="text-xs text-ink-500 dark:text-ink-400">No status</span>
}
