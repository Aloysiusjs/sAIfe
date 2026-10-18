/**
 * Storage for the dashboard: Upstash Redis over its REST API (plain fetch, no client
 * library). Vercel's Upstash integration sets KV_REST_API_URL / KV_REST_API_TOKEN;
 * UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN work too.
 *
 * Local development (`npm run dev`) has no Redis: the Vite dev server sets
 * SAIFE_DASHBOARD_DEV=1 and an in-memory store is used instead. In production a missing
 * Redis configuration is an error (503), never a silent in-memory fallback.
 *
 * Keys (all per-run keys expire after RETENTION_DAYS):
 *   saife:runs            ZSET  run id -> started_at (ms)
 *   saife:run:<id>        JSON  RunMeta
 *   saife:ev:<id>         HASH  seq -> event JSON (idempotent under webhook retries)
 *   saife:report:<id>     JSON  full report from run.completed
 *   saife:bench           ZSET  bench name -> received_at (ms)
 *   saife:bench:<name>    JSON  BenchSummary
 */

export type Cmd = (string | number)[]

export interface Store {
  kind: 'upstash' | 'memory'
  pipeline(cmds: Cmd[]): Promise<unknown[]>
}

export const RETENTION_DAYS = 30
export const RETENTION_S = RETENTION_DAYS * 24 * 3600
export const MAX_RUNS = 500
export const MAX_BENCH = 100

export const K = {
  runs: 'saife:runs',
  run: (id: string) => `saife:run:${id}`,
  events: (id: string) => `saife:ev:${id}`,
  report: (id: string) => `saife:report:${id}`,
  bench: 'saife:bench',
  benchItem: (name: string) => `saife:bench:${name}`,
}

class UpstashStore implements Store {
  kind = 'upstash' as const
  private url: string
  private token: string

  constructor(url: string, token: string) {
    this.url = url
    this.token = token
  }

  async pipeline(cmds: Cmd[]): Promise<unknown[]> {
    if (cmds.length === 0) return []
    const res = await fetch(`${this.url.replace(/\/$/, '')}/pipeline`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(cmds),
    })
    if (!res.ok) throw new Error(`storage error: HTTP ${res.status}`)
    const out = (await res.json()) as { result?: unknown; error?: string }[]
    return out.map((r) => {
      if (r.error) throw new Error(`storage error: ${r.error}`)
      return r.result ?? null
    })
  }
}

/** Just enough Redis for local development and tests. */
export class MemoryStore implements Store {
  kind = 'memory' as const
  private strings = new Map<string, string>()
  private hashes = new Map<string, Map<string, string>>()
  private zsets = new Map<string, Map<string, number>>()

  private zsorted(key: string): string[] {
    const z = this.zsets.get(key) ?? new Map<string, number>()
    return [...z.entries()].sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0])).map(([m]) => m)
  }

  private one(cmd: Cmd): unknown {
    const [name, ...a] = cmd.map(String)
    const key = a[0] ?? ''
    switch ((name ?? '').toUpperCase()) {
      case 'GET':
        return this.strings.get(key) ?? null
      case 'SET':
        this.strings.set(key, a[1] ?? '')
        return 'OK'
      case 'MGET':
        return a.map((k) => this.strings.get(k) ?? null)
      case 'DEL':
        return a.reduce((n, k) => n + Number(this.strings.delete(k) || this.hashes.delete(k) || this.zsets.delete(k)), 0)
      case 'EXPIRE':
        return 1
      case 'HSET': {
        const h = this.hashes.get(key) ?? new Map<string, string>()
        let added = 0
        for (let i = 1; i + 1 < a.length; i += 2) {
          if (!h.has(a[i]!)) added++
          h.set(a[i]!, a[i + 1]!)
        }
        this.hashes.set(key, h)
        return added
      }
      case 'HGETALL':
        return [...(this.hashes.get(key) ?? new Map<string, string>()).entries()].flat()
      case 'ZADD': {
        const z = this.zsets.get(key) ?? new Map<string, number>()
        z.set(a[2]!, Number(a[1]))
        this.zsets.set(key, z)
        return 1
      }
      case 'ZREVRANGE': {
        const all = this.zsorted(key).reverse()
        const stop = Number(a[2])
        return all.slice(Number(a[1]), stop < 0 ? all.length + stop + 1 : stop + 1)
      }
      case 'ZREMRANGEBYRANK': {
        const all = this.zsorted(key)
        const start = Number(a[1])
        const stop = Number(a[2])
        const s = start < 0 ? all.length + start : start
        const e = stop < 0 ? all.length + stop : stop
        const doomed = all.slice(Math.max(0, s), e + 1)
        for (const m of doomed) this.zsets.get(key)?.delete(m)
        return doomed.length
      }
      case 'ZCARD':
        return this.zsets.get(key)?.size ?? 0
      case 'PING':
        return 'PONG'
      default:
        throw new Error(`memory store: unsupported command ${name}`)
    }
  }

  async pipeline(cmds: Cmd[]): Promise<unknown[]> {
    return cmds.map((c) => this.one(c))
  }
}

type Env = Record<string, string | undefined>

export function storeFromEnv(env: Env = process.env): Store | null {
  const url = env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL
  const token = env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN
  if (url && token) return new UpstashStore(url, token)
  if (env.SAIFE_DASHBOARD_DEV === '1') {
    const g = globalThis as { __saifeMemoryStore?: MemoryStore }
    g.__saifeMemoryStore ??= new MemoryStore()
    return g.__saifeMemoryStore
  }
  return null
}

export function parseJson<T>(raw: unknown): T | null {
  if (typeof raw !== 'string') return null
  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

/** HGETALL replies as a flat [field, value, ...] array over REST. */
export function pairs(raw: unknown): [string, string][] {
  if (!Array.isArray(raw)) return []
  const out: [string, string][] = []
  for (let i = 0; i + 1 < raw.length; i += 2) out.push([String(raw[i]), String(raw[i + 1])])
  return out
}
