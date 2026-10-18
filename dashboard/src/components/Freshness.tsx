import { RefreshCw, TriangleAlert } from 'lucide-react'
import { useNow } from '../lib/usePolling'

interface Props {
  updatedAt: number | null
  loading: boolean
  stale: boolean
  onRefresh: () => void
}

/** "Updated 4 s ago" + refresh button, with a warning when the last poll failed. */
export function Freshness({ updatedAt, loading, stale, onRefresh }: Props) {
  const now = useNow(1000)
  const ago = updatedAt === null ? null : Math.max(0, Math.round((now - updatedAt) / 1000))
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500 dark:text-ink-400">
      {stale ? (
        <span className="inline-flex items-center gap-1 text-likely-fg dark:text-likely-bg" role="status">
          <TriangleAlert aria-hidden="true" className="h-3.5 w-3.5" />
          Could not refresh; showing the last data
        </span>
      ) : null}
      <span aria-live="off">{ago === null ? 'Not loaded yet' : ago < 2 ? 'Updated just now' : `Updated ${ago} s ago`}</span>
      <button type="button" className="btn px-2 py-1 text-xs" onClick={onRefresh} disabled={loading}>
        <RefreshCw aria-hidden="true" className={`h-3.5 w-3.5 ${loading ? 'motion-safe:animate-spin' : ''}`} />
        Refresh
      </button>
    </div>
  )
}
