import { useCallback, useEffect, useRef, useState } from 'react'

export interface Polled<T> {
  data: T | null
  /** Last error; `data` keeps the last good value so stale data stays on screen. */
  error: unknown
  loading: boolean
  /** Date.now() of the last successful fetch. */
  updatedAt: number | null
  refresh: () => void
}

/**
 * Fetch now and then every `intervalMs` while the tab is visible; pause while hidden
 * and refetch on return. `intervalMs = null` fetches once (plus manual refreshes).
 * A change of `key` resets the data.
 */
export function usePolling<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number | null,
): Polled<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const inflight = useRef<AbortController | null>(null)

  const run = useCallback(() => {
    inflight.current?.abort()
    const ctl = new AbortController()
    inflight.current = ctl
    setLoading(true)
    fetcherRef
      .current(ctl.signal)
      .then((d) => {
        if (ctl.signal.aborted) return
        setData(d)
        setError(null)
        setUpdatedAt(Date.now())
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return
        setError(e)
      })
      .finally(() => {
        if (!ctl.signal.aborted) setLoading(false)
      })
  }, [])

  useEffect(() => {
    setData(null)
    setError(null)
    setUpdatedAt(null)
    run()
    return () => inflight.current?.abort()
  }, [key, run])

  useEffect(() => {
    if (intervalMs === null) return
    let timer: number | undefined
    const start = () => {
      if (timer !== undefined) return
      timer = window.setInterval(() => {
        if (document.visibilityState === 'visible') run()
      }, intervalMs)
    }
    const stop = () => {
      if (timer !== undefined) window.clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        run()
        start()
      } else {
        stop()
      }
    }
    if (document.visibilityState === 'visible') start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [key, intervalMs, run])

  return { data, error, loading, updatedAt, refresh: run }
}

/** Re-render every `ms` so relative times ("5 s ago") stay current. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(t)
  }, [ms])
  return now
}
