import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  applyEvent,
  checkBearer,
  hmacHex,
  parseBench,
  parseEvent,
  safeEqual,
  verifySignature,
  type SaifeEvent,
} from '../api/_lib/core.ts'
import { MemoryStore } from '../api/_lib/store.ts'
import { getRun, health, ingest, ingestBench, listBench, listRuns, type Deps } from '../api/_lib/service.ts'

const SECRET = 'test-secret-0123456789'
const PASSWORD = 'correct horse battery staple'

function deps(over: Partial<Deps> = {}): Deps {
  return {
    env: { SAIFE_DASHBOARD_SECRET: SECRET, DASHBOARD_PASSWORD: PASSWORD },
    store: new MemoryStore(),
    now: () => 1_800_000_000,
    ...over,
  }
}

async function post(path: string, body: unknown, d: Deps, secret = SECRET): Promise<Response> {
  const text = JSON.stringify(body)
  const req = new Request(`https://x.test${path}`, {
    method: 'POST',
    headers: { 'X-Saife-Signature': `sha256=${await hmacHex(secret, text)}`, 'Content-Type': 'application/json' },
    body: text,
  })
  return path === '/api/bench' ? ingestBench(req, d) : ingest(req, d)
}

function get(path: string, password: string | null = PASSWORD): Request {
  return new Request(`https://x.test${path}`, {
    headers: password === null ? {} : { Authorization: `Bearer ${password}` },
  })
}

const ev = (type: SaifeEvent['type'], seq: number, data: Record<string, unknown> = {}, run_id = 'r1'): SaifeEvent => ({
  type,
  run_id,
  seq,
  ts: 1_800_000_000 + seq,
  data,
})

const verdict = (id: string, status: string, model = 'verifier/gemini-3.1-flash-lite') => ({
  pass_index: 0,
  result: {
    claim: { id, text: `claim ${id}` },
    status,
    verdicts: [
      { detector: 'grounding', verdict: 'SUPPORTED', model },
      { detector: 'deterministic', verdict: 'NOT_FOUND', model: null },
    ],
  },
})

const REPORT = {
  run_id: 'r1',
  status: 'CAUTION',
  preset: 'balanced',
  question: 'When was it built?',
  final_answer: 'It was completed in 1889.',
  claims: [
    { claim: { id: 'a' }, status: 'SUPPORTED', verdicts: [{ detector: 'grounding', model: 'verifier/gemini' }] },
    { claim: { id: 'b' }, status: 'CONTRADICTED', verdicts: [{ detector: 'grounding', model: 'verifier/gemini' }] },
    { claim: { id: 'c' }, status: 'UNSUPPORTED', verdicts: [] },
    { claim: { id: 'd' }, status: 'LIKELY', verdicts: [{ detector: 'consistency', model: 'openrouter/nvidia/x:free' }] },
  ],
  repairs: [{ op: 'correct' }, { op: 'remove' }],
  alerts: [{ severity: 'high' }, { severity: 'warning' }],
  passes: [{}, {}],
  usage: { input_tokens: 100, output_tokens: 20, cost_usd: 0.0123, wall_time_s: 11.2, by_model: { 'verifier/gemini': {} } },
  risk: 0.6,
  stop_cause: 'resolved',
  verifier_diversity: true,
  fallbacks: [],
}

// --- core -----------------------------------------------------------------------------

test('HMAC matches sAIfe\'s Python signer byte for byte (non-ASCII included)', async () => {
  // Vector from saife.notify.webhook.sign('s3cret', body.encode('utf-8')).
  const body = '{"type":"run.started","run_id":"r1","seq":0,"ts":1.5,"data":{"question":"Is it 1889? — yes"}}'
  const expected = 'sha256=a996901ff1ba396077d2db4d721cf313f13494949aa720b31316e56e1923821b'
  assert.equal(`sha256=${await hmacHex('s3cret', body)}`, expected)
  assert.equal(await verifySignature('s3cret', body, expected), true)
  assert.equal(await verifySignature('s3cret', body + ' ', expected), false)
  assert.equal(await verifySignature('other', body, expected), false)
  assert.equal(await verifySignature('s3cret', body, null), false)
  assert.equal(await verifySignature('', body, expected), false)
})

test('password checks are exact and never accept an empty password', () => {
  assert.equal(checkBearer('pw', 'Bearer pw'), true)
  assert.equal(checkBearer('pw', 'Bearer pw '), false)
  assert.equal(checkBearer('pw', 'pw'), false)
  assert.equal(checkBearer('', 'Bearer '), false)
  assert.equal(checkBearer(undefined, 'Bearer x'), false)
  assert.equal(safeEqual('abc', 'abd'), false)
})

test('parseEvent rejects malformed or unsafe events', () => {
  assert.ok(parseEvent(ev('run.started', 0)))
  assert.equal(parseEvent({ ...ev('run.started', 0), type: 'run.hacked' }), null)
  assert.equal(parseEvent({ ...ev('run.started', 0), run_id: '../etc/passwd' }), null)
  assert.equal(parseEvent({ ...ev('run.started', 0), run_id: 'a'.repeat(65) }), null)
  assert.equal(parseEvent({ ...ev('run.started', 0), seq: -1 }), null)
  assert.equal(parseEvent({ ...ev('run.started', 0), seq: 1.5 }), null)
  assert.equal(parseEvent('nope'), null)
})

test('live counts follow the latest verdict per claim; the report decides the final summary', () => {
  let m = applyEvent(null, ev('run.started', 0, { question: 'q', preset: 'balanced', mode: 'check', sources: [1, 2] }))
  assert.equal(m.state, 'running')
  assert.equal(m.n_sources, 2)
  m = applyEvent(m, ev('claim.verdict', 1, verdict('a', 'UNSUPPORTED')))
  m = applyEvent(m, ev('claim.verdict', 2, verdict('b', 'CONTRADICTED')))
  m = applyEvent(m, ev('claim.verdict', 3, verdict('a', 'SUPPORTED'))) // re-verified in pass 2
  assert.deepEqual([m.counts.SUPPORTED, m.counts.UNSUPPORTED, m.counts.CONTRADICTED], [1, 0, 1])
  assert.deepEqual(m.judges, ['verifier/gemini-3.1-flash-lite']) // deterministic excluded
  m = applyEvent(m, ev('repair.applied', 4, { repair: {} }))
  m = applyEvent(m, ev('alert.raised', 5, { alert: { severity: 'high' } }))
  assert.equal(m.repairs, 1)
  assert.equal(m.alerts.high, 1)

  const done = applyEvent(m, ev('run.completed', 6, { status: 'CAUTION', report: REPORT }))
  assert.equal(done.state, 'completed')
  assert.equal(done.status, 'CAUTION')
  assert.deepEqual(done.live, {})
  assert.deepEqual(done.counts, { SUPPORTED: 1, LIKELY: 1, UNSUPPORTED: 1, CONTRADICTED: 1, UNVERIFIABLE: 0 })
  assert.equal(done.repairs, 2)
  assert.deepEqual(done.alerts, { info: 0, warning: 1, high: 1 })
  assert.deepEqual(done.judges, ['openrouter/nvidia/x:free', 'verifier/gemini', 'verifier/gemini-3.1-flash-lite'])
  assert.equal(done.tokens, 120)
  assert.equal(done.cost_usd, 0.0123)
  assert.equal(done.passes, 2)

  // Late events after completion don't reopen or change the run.
  const late = applyEvent(done, ev('claim.verdict', 7, verdict('z', 'CONTRADICTED')))
  assert.deepEqual(late.counts, done.counts)
})

test('a failed run records its error', () => {
  const m = applyEvent(applyEvent(null, ev('run.started', 0)), ev('run.failed', 1, { error: 'LLMUnavailableError: down' }))
  assert.equal(m.state, 'failed')
  assert.equal(m.error, 'LLMUnavailableError: down')
})

test('parseBench keeps metrics, drops items and silent-failure ids, validates the name', () => {
  const raw = {
    meta: { run_name: '2026-10-10-seeded-balanced', date: '2026-10-10', set: 'seeded', n_items: 5, models: { generate: 'x' } },
    metrics: { precision: 1, recall: 1, silent_failures: 0, silent_failure_ids: ['a'] },
    items: [{ big: true }],
  }
  const b = parseBench(raw, 5)
  assert.ok(b)
  assert.equal(b.name, '2026-10-10-seeded-balanced')
  assert.equal('silent_failure_ids' in b.metrics, false)
  assert.equal('items' in (b as unknown as Record<string, unknown>), false)
  assert.equal(parseBench({ ...raw, meta: { ...raw.meta, run_name: '../x' } }, 5), null)
})

// --- service --------------------------------------------------------------------------

test('ingest needs a valid signature and a configured secret', async () => {
  const d = deps()
  assert.equal((await post('/api/ingest', ev('run.started', 0), d, 'wrong')).status, 401)
  const unsigned = new Request('https://x.test/api/ingest', { method: 'POST', body: JSON.stringify(ev('run.started', 0)) })
  assert.equal((await ingest(unsigned, d)).status, 401)
  assert.equal((await post('/api/ingest', ev('run.started', 0), deps({ env: { DASHBOARD_PASSWORD: PASSWORD } }))).status, 503)
  assert.equal((await post('/api/ingest', ev('run.started', 0), deps({ store: null }))).status, 503)
  assert.equal((await post('/api/ingest', { hello: 'world' }, d)).status, 400)
})

test('oversized bodies are refused before parsing', async () => {
  const big = JSON.stringify({ ...ev('run.started', 0), data: { pad: 'x'.repeat(1_100_000) } })
  const req = new Request('https://x.test/api/ingest', {
    method: 'POST',
    headers: { 'X-Saife-Signature': `sha256=${await hmacHex(SECRET, big)}` },
    body: big,
  })
  assert.equal((await ingest(req, deps())).status, 413)
})

test('a full run flows from ingest to the read API; retries are idempotent', async () => {
  const d = deps()
  for (const e of [
    ev('run.started', 0, { question: 'When was it built?', preset: 'balanced', mode: 'check', sources: [] }),
    ev('claim.verdict', 1, verdict('b', 'CONTRADICTED')),
    ev('claim.verdict', 1, verdict('b', 'CONTRADICTED')), // webhook retry of the same seq
    ev('run.completed', 2, { status: 'CAUTION', report: REPORT }),
  ]) {
    assert.equal((await post('/api/ingest', e, d)).status, 200)
  }
  assert.equal((await listRuns(get('/api/runs'), d)).status, 200)
  const { runs } = (await (await listRuns(get('/api/runs'), d)).json()) as { runs: { id: string; status: string }[] }
  assert.deepEqual(runs.map((r) => [r.id, r.status]), [['r1', 'CAUTION']])

  const detail = (await (await getRun(get('/api/run?id=r1'), d)).json()) as {
    meta: { state: string }
    report: { final_answer: string } | null
    events: { seq: number; data: Record<string, unknown> }[]
  }
  assert.equal(detail.meta.state, 'completed')
  assert.equal(detail.report?.final_answer, 'It was completed in 1889.')
  assert.deepEqual(detail.events.map((e) => e.seq), [0, 1, 2]) // deduplicated, ordered
  assert.equal('report' in detail.events[2]!.data, false) // report stored once, not twice
})

test('reads need the password, and never run open when it is unset', async () => {
  const d = deps()
  assert.equal((await listRuns(get('/api/runs', null), d)).status, 401)
  assert.equal((await listRuns(get('/api/runs', 'guess'), d)).status, 401)
  assert.equal((await getRun(get('/api/run?id=r1', 'guess'), d)).status, 401)
  assert.equal((await listBench(get('/api/bench', null), d)).status, 401)
  const open = deps({ env: { SAIFE_DASHBOARD_SECRET: SECRET } })
  assert.equal((await listRuns(get('/api/runs', ''), open)).status, 503)
  assert.equal((await getRun(get('/api/run?id=../x'), d)).status, 400)
  assert.equal((await getRun(get('/api/run?id=missing'), d)).status, 404)
})

test('runs are listed newest first and capped', async () => {
  const d = deps()
  for (let i = 0; i < 3; i++) {
    await post('/api/ingest', { ...ev('run.started', 0, {}, `run${i}`), ts: 1_800_000_000 + i * 60 }, d)
  }
  const body = (await (await listRuns(get('/api/runs?limit=2'), d)).json()) as { runs: { id: string }[] }
  assert.deepEqual(body.runs.map((r) => r.id), ['run2', 'run1'])
})

test('benchmark results can be published and listed', async () => {
  const d = deps()
  const result = { meta: { run_name: 'b1', date: '2026-10-18' }, metrics: { recall: 0.9 } }
  assert.equal((await post('/api/bench', result, d)).status, 200)
  assert.equal((await post('/api/bench', { meta: {} }, d)).status, 400)
  const body = (await (await listBench(get('/api/bench'), d)).json()) as { bench: { name: string }[] }
  assert.deepEqual(body.bench.map((b) => b.name), ['b1'])
})

test('health reports presence of settings, never their values', async () => {
  const body = (await (await health(deps())).json()) as Record<string, unknown>
  assert.deepEqual(body, { ok: true, storage: 'memory', ingest_secret: true, password: true, retention_days: 30 })
  assert.equal(JSON.stringify(body).includes(SECRET), false)
  const bare = (await (await health(deps({ env: {}, store: null }))).json()) as Record<string, unknown>
  assert.equal(bare.ok, false)
  assert.equal(bare.storage, 'missing')
})
