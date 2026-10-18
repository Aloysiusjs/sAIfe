/** Typed client for the dashboard's read API (same origin). */

import type { BenchSummary, RunMeta, SaifeEvent } from '../api/_lib/core'

export interface Health {
  ok: boolean
  storage: 'upstash' | 'memory' | 'missing' | 'error'
  ingest_secret: boolean
  password: boolean
  retention_days: number
}

export interface RunsResponse {
  runs: RunMeta[]
  now: number
}

export interface RunResponse {
  meta: RunMeta
  report: Record<string, unknown> | null
  events: SaifeEvent[]
  now: number
}

export interface BenchResponse {
  bench: BenchSummary[]
}

export type ApiErrorKind = 'unauthorized' | 'not_configured' | 'not_found' | 'network' | 'server'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number
  constructor(kind: ApiErrorKind, status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
  }
}

export class UnauthorizedError extends ApiError {
  constructor(message: string) {
    super('unauthorized', 401, message)
  }
}
export class NotConfiguredError extends ApiError {
  constructor(message: string) {
    super('not_configured', 503, message)
  }
}
export class NotFoundError extends ApiError {
  constructor(message: string) {
    super('not_found', 404, message)
  }
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

async function request(path: string, password: string | null, signal?: AbortSignal): Promise<unknown> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (password !== null) headers.Authorization = `Bearer ${password}`
  let res: Response
  try {
    res = await fetch(path, { headers, signal, cache: 'no-store' })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError('network', 0, 'The dashboard API could not be reached.')
  }
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (res.ok) return body
  const message = isObj(body) && typeof body.error === 'string' ? body.error : `HTTP ${res.status}`
  if (res.status === 401) throw new UnauthorizedError(message)
  if (res.status === 503) throw new NotConfiguredError(message)
  if (res.status === 404) throw new NotFoundError(message)
  throw new ApiError('server', res.status, message)
}

function shapeError(what: string): ApiError {
  return new ApiError('server', 200, `Unexpected ${what} response from the API.`)
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const b = await request('/api/health', null, signal)
  if (!isObj(b) || typeof b.ok !== 'boolean') throw shapeError('health')
  const storage = b.storage === 'upstash' || b.storage === 'memory' || b.storage === 'error' ? b.storage : 'missing'
  return {
    ok: b.ok,
    storage,
    ingest_secret: b.ingest_secret === true,
    password: b.password === true,
    retention_days: typeof b.retention_days === 'number' ? b.retention_days : 0,
  }
}

export async function getRuns(password: string, signal?: AbortSignal): Promise<RunsResponse> {
  const b = await request('/api/runs?limit=200', password, signal)
  if (!isObj(b) || !Array.isArray(b.runs)) throw shapeError('runs')
  return {
    runs: b.runs.filter((r): r is RunMeta => isObj(r) && typeof r.id === 'string' && isObj(r.counts)),
    now: typeof b.now === 'number' ? b.now : Date.now() / 1000,
  }
}

export async function getRun(password: string, id: string, signal?: AbortSignal): Promise<RunResponse> {
  const b = await request(`/api/run?id=${encodeURIComponent(id)}`, password, signal)
  if (!isObj(b) || !isObj(b.meta)) throw shapeError('run')
  const events = Array.isArray(b.events)
    ? b.events.filter((e): e is SaifeEvent => isObj(e) && typeof e.type === 'string' && isObj(e.data))
    : []
  return {
    meta: b.meta as unknown as RunMeta,
    report: isObj(b.report) ? b.report : null,
    events,
    now: typeof b.now === 'number' ? b.now : Date.now() / 1000,
  }
}

export async function getBench(password: string, signal?: AbortSignal): Promise<BenchResponse> {
  const b = await request('/api/bench', password, signal)
  if (!isObj(b) || !Array.isArray(b.bench)) throw shapeError('bench')
  return { bench: b.bench.filter((x): x is BenchSummary => isObj(x) && typeof x.name === 'string') }
}
