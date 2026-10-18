/**
 * Request handling for the dashboard API, independent of Vercel so it can be tested
 * with a MemoryStore and plain `Request` objects. Each api/*.ts file is a thin wrapper.
 *
 * Writes (/api/ingest, POST /api/bench) need an HMAC signature made with
 * SAIFE_DASHBOARD_SECRET, the same secret configured in sAIfe's .env. Reads need
 * `Authorization: Bearer <DASHBOARD_PASSWORD>`. If either setting is missing the
 * endpoint answers 503: the dashboard never runs open.
 */

import {
  MAX_BODY_BYTES,
  RUN_ID_RE,
  applyEvent,
  checkBearer,
  parseBench,
  parseEvent,
  verifySignature,
  type BenchSummary,
  type RunMeta,
  type SaifeEvent,
} from './core.js'
import { K, MAX_BENCH, MAX_RUNS, RETENTION_S, pairs, parseJson, storeFromEnv, type Store } from './store.js'

type Env = Record<string, string | undefined>

export interface Deps {
  env: Env
  store: Store | null
  now: () => number // unix seconds
}

export function defaultDeps(): Deps {
  return { env: process.env, store: storeFromEnv(process.env), now: () => Date.now() / 1000 }
}

const SECURITY_HEADERS = {
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...SECURITY_HEADERS },
  })
}

const err = (status: number, message: string) => json({ error: message }, status)

async function readBody(req: Request): Promise<string | Response> {
  const declared = Number(req.headers.get('content-length') ?? '0')
  if (declared > MAX_BODY_BYTES) return err(413, 'body too large')
  const text = await req.text()
  if (new TextEncoder().encode(text).length > MAX_BODY_BYTES) return err(413, 'body too large')
  return text
}

async function signedJson(req: Request, deps: Deps): Promise<unknown | Response> {
  const secret = deps.env.SAIFE_DASHBOARD_SECRET
  if (!secret) return err(503, 'SAIFE_DASHBOARD_SECRET is not configured')
  if (!deps.store) return err(503, 'storage is not configured')
  const body = await readBody(req)
  if (body instanceof Response) return body
  if (!(await verifySignature(secret, body, req.headers.get('x-saife-signature')))) {
    return err(401, 'bad signature')
  }
  try {
    return JSON.parse(body) as unknown
  } catch {
    return err(400, 'body is not JSON')
  }
}

function authorize(req: Request, deps: Deps): Response | null {
  if (!deps.env.DASHBOARD_PASSWORD) return err(503, 'DASHBOARD_PASSWORD is not configured')
  if (!checkBearer(deps.env.DASHBOARD_PASSWORD, req.headers.get('authorization'))) {
    return err(401, 'wrong or missing password')
  }
  if (!deps.store) return err(503, 'storage is not configured')
  return null
}

// --- Writes ---------------------------------------------------------------------------

export async function ingest(req: Request, deps: Deps): Promise<Response> {
  const parsed = await signedJson(req, deps)
  if (parsed instanceof Response) return parsed
  const ev = parseEvent(parsed)
  if (!ev) return err(400, 'not a sAIfe event')
  const store = deps.store!
  const [rawMeta] = await store.pipeline([['GET', K.run(ev.run_id)]])
  const meta = applyEvent(parseJson<RunMeta>(rawMeta), ev)
  const cmds: (string | number)[][] = [
    ['HSET', K.events(ev.run_id), String(ev.seq), JSON.stringify(stripEvent(ev))],
    ['EXPIRE', K.events(ev.run_id), RETENTION_S],
    ['SET', K.run(ev.run_id), JSON.stringify(meta)],
    ['EXPIRE', K.run(ev.run_id), RETENTION_S],
  ]
  if (ev.type === 'run.started' || !rawMeta) {
    cmds.push(['ZADD', K.runs, Math.round(meta.started_at * 1000), ev.run_id])
    cmds.push(['ZREMRANGEBYRANK', K.runs, 0, -(MAX_RUNS + 1)])
  }
  if (ev.type === 'run.completed' && typeof ev.data.report === 'object' && ev.data.report !== null) {
    cmds.push(['SET', K.report(ev.run_id), JSON.stringify(ev.data.report)])
    cmds.push(['EXPIRE', K.report(ev.run_id), RETENTION_S])
  }
  await store.pipeline(cmds)
  return json({ ok: true, run_id: ev.run_id, state: meta.state })
}

/** The report is stored once under its own key; the event copy keeps only the status. */
function stripEvent(ev: SaifeEvent): SaifeEvent {
  if (ev.type !== 'run.completed') return ev
  const { report: _report, ...rest } = ev.data
  return { ...ev, data: rest }
}

export async function ingestBench(req: Request, deps: Deps): Promise<Response> {
  const parsed = await signedJson(req, deps)
  if (parsed instanceof Response) return parsed
  const bench = parseBench(parsed, deps.now())
  if (!bench) return err(400, 'not a sAIfe benchmark result')
  await deps.store!.pipeline([
    ['SET', K.benchItem(bench.name), JSON.stringify(bench)],
    ['ZADD', K.bench, Math.round(bench.received_at * 1000), bench.name],
    ['ZREMRANGEBYRANK', K.bench, 0, -(MAX_BENCH + 1)],
  ])
  return json({ ok: true, name: bench.name })
}

// --- Reads ----------------------------------------------------------------------------

export async function listRuns(req: Request, deps: Deps): Promise<Response> {
  const denied = authorize(req, deps)
  if (denied) return denied
  const limit = Math.min(Math.max(Number(new URL(req.url).searchParams.get('limit') ?? '200') || 200, 1), MAX_RUNS)
  const store = deps.store!
  const [ids] = await store.pipeline([['ZREVRANGE', K.runs, 0, limit - 1]])
  const list = Array.isArray(ids) ? ids.map(String) : []
  if (list.length === 0) return json({ runs: [], now: deps.now() })
  const [metas] = await store.pipeline([['MGET', ...list.map(K.run)]])
  const runs = (Array.isArray(metas) ? metas : [])
    .map((m) => parseJson<RunMeta>(m))
    .filter((m): m is RunMeta => m !== null)
  return json({ runs, now: deps.now() })
}

export async function getRun(req: Request, deps: Deps): Promise<Response> {
  const denied = authorize(req, deps)
  if (denied) return denied
  const id = new URL(req.url).searchParams.get('id') ?? ''
  if (!RUN_ID_RE.test(id)) return err(400, 'bad run id')
  const [meta, report, events] = await deps.store!.pipeline([
    ['GET', K.run(id)],
    ['GET', K.report(id)],
    ['HGETALL', K.events(id)],
  ])
  const m = parseJson<RunMeta>(meta)
  if (!m) return err(404, 'run not found (it may have expired)')
  const evs = pairs(events)
    .map(([, v]) => parseJson<SaifeEvent>(v))
    .filter((e): e is SaifeEvent => e !== null)
    .sort((a, b) => a.seq - b.seq)
  return json({ meta: m, report: parseJson<Record<string, unknown>>(report), events: evs, now: deps.now() })
}

export async function listBench(req: Request, deps: Deps): Promise<Response> {
  const denied = authorize(req, deps)
  if (denied) return denied
  const store = deps.store!
  const [names] = await store.pipeline([['ZREVRANGE', K.bench, 0, MAX_BENCH - 1]])
  const list = Array.isArray(names) ? names.map(String) : []
  if (list.length === 0) return json({ bench: [] })
  const [items] = await store.pipeline([['MGET', ...list.map(K.benchItem)]])
  const bench = (Array.isArray(items) ? items : [])
    .map((b) => parseJson<BenchSummary>(b))
    .filter((b): b is BenchSummary => b !== null)
  return json({ bench })
}

/** Unauthenticated: says which settings are present (never their values). */
export async function health(deps: Deps): Promise<Response> {
  let storage: 'upstash' | 'memory' | 'missing' | 'error' = deps.store ? deps.store.kind : 'missing'
  if (deps.store) {
    try {
      await deps.store.pipeline([['PING']])
    } catch {
      storage = 'error'
    }
  }
  return json({
    ok: storage !== 'missing' && storage !== 'error' && !!deps.env.SAIFE_DASHBOARD_SECRET && !!deps.env.DASHBOARD_PASSWORD,
    storage,
    ingest_secret: !!deps.env.SAIFE_DASHBOARD_SECRET,
    password: !!deps.env.DASHBOARD_PASSWORD,
    retention_days: RETENTION_S / 86400,
  })
}
