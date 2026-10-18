import type { ResultSummary } from '../components/results/types'
import type { GuardConfig, Report, RunEvent, RunRequest, Trap } from './types'
import { EVENT_TYPES } from './types'

const BASE = '/api'

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const ctrl = new AbortController()
  const timer = window.setTimeout(() => ctrl.abort(), init?.timeoutMs ?? 10_000)
  try {
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}) },
    })
    if (!res.ok) {
      let detail = res.statusText
      try {
        const body = (await res.json()) as { detail?: unknown }
        if (typeof body.detail === 'string') detail = body.detail
      } catch {
        // non-JSON error body; keep the status text
      }
      throw new ApiError(res.status, detail || `HTTP ${res.status}`)
    }
    const type = res.headers.get('content-type') ?? ''
    if (!type.includes('json')) throw new ApiError(res.status, 'The server did not return JSON.')
    return (await res.json()) as T
  } finally {
    window.clearTimeout(timer)
  }
}

export const api = {
  presets: () => request<Record<string, GuardConfig>>('/presets', { timeoutMs: 3_000 }),
  traps: () => request<Trap[]>('/traps'),
  createRun: (body: RunRequest) =>
    request<{ run_id: string }>('/runs', { method: 'POST', body: JSON.stringify(body) }),
  report: (id: string) => request<Report>(`/runs/${encodeURIComponent(id)}`),
  /** Benchmark summaries from bench/results/*.json, newest first. */
  results: () => request<ResultSummary[]>('/results'),
}

export interface StreamHandlers {
  onEvent: (event: RunEvent) => void
  onError: (message: string) => void
}

/**
 * Subscribe to a run's SSE stream. The server replays past events first, so a
 * reconnect re-sends history: events are de-duplicated by `seq`.
 * Returns a function that closes the stream.
 */
export function streamRun(runId: string, { onEvent, onError }: StreamHandlers): () => void {
  const source = new EventSource(`${BASE}/runs/${encodeURIComponent(runId)}/events`)
  let lastSeq = -1
  let done = false
  let failures = 0

  const close = () => {
    done = true
    source.close()
  }

  const handle = (msg: MessageEvent<string>) => {
    let event: RunEvent
    try {
      event = JSON.parse(msg.data) as RunEvent
    } catch {
      return
    }
    failures = 0
    if (event.seq <= lastSeq) return
    lastSeq = event.seq
    onEvent(event)
    if (event.type === 'run.completed' || event.type === 'run.failed') close()
  }

  for (const type of EVENT_TYPES) source.addEventListener(type, handle as EventListener)

  source.onerror = () => {
    if (done) return
    failures += 1
    // EventSource retries on its own; give up if the server refuses outright or keeps dropping.
    if (source.readyState === EventSource.CLOSED || failures >= 5) {
      close()
      onError('Lost the connection to the sAIfe server before the run finished.')
    }
  }

  return close
}

/** True when the API answers. Used to fall back to demo mode when it doesn't. */
export async function probeApi(): Promise<Record<string, GuardConfig> | null> {
  try {
    return await api.presets()
  } catch {
    return null
  }
}
