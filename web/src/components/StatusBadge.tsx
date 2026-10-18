import type { ClaimStatus } from '../api/types'
import { CLAIM_STATUS, PENDING_META } from '../lib/status'

interface Props {
  status: ClaimStatus | null
  size?: 'sm' | 'md'
  className?: string
}

/** Icon + label + colour. Never colour alone (N5). `null` means the claim is still being checked. */
export function StatusBadge({ status, size = 'sm', className = '' }: Props) {
  const meta = status ? CLAIM_STATUS[status] : PENDING_META
  const Icon = meta.icon
  const pad = size === 'sm' ? 'gap-1 px-1.5 py-0.5 text-2xs' : 'gap-1.5 px-2 py-1 text-xs'
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded font-semibold uppercase tracking-wide ${pad} ${meta.tint} ${meta.text} ${className}`}
    >
      <Icon
        aria-hidden="true"
        className={`${size === 'sm' ? 'h-3 w-3' : 'h-3.5 w-3.5'} ${status ? '' : 'motion-safe:animate-spin'}`}
        strokeWidth={2.25}
      />
      {meta.label}
    </span>
  )
}
