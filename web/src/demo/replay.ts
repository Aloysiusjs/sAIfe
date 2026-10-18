import type { RunEvent, Source } from '../api/types'

export interface DemoRun {
  question: string
  preset: string
  sources: Source[]
  events: RunEvent[]
}

let cached: Promise<DemoRun> | null = null

/** The canned run is a separate chunk: only demo mode pays for it. */
export function loadDemoRun(): Promise<DemoRun> {
  cached ??= import('./run.json').then((m) => m.default as unknown as DemoRun)
  return cached
}

const MIN_GAP_MS = 120
const MAX_GAP_MS = 1100

/**
 * Replay the canned events with their recorded rhythm, compressed so the whole run
 * takes a few seconds. Returns a cancel function.
 */
export function replayDemo(run: DemoRun, onEvent: (e: RunEvent) => void, speed = 0.45): () => void {
  let cancelled = false
  let timer = 0
  let i = 0
  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

  const step = () => {
    if (cancelled) return
    const event = run.events[i]
    if (!event) return
    onEvent(event)
    i += 1
    const next = run.events[i]
    if (!next) return
    const gap = reduceMotion ? MIN_GAP_MS : Math.min(MAX_GAP_MS, Math.max(MIN_GAP_MS, (next.ts - event.ts) * 1000 * speed))
    timer = window.setTimeout(step, gap)
  }
  timer = window.setTimeout(step, 250)
  return () => {
    cancelled = true
    window.clearTimeout(timer)
  }
}
