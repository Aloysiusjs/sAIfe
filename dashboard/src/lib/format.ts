/** Number, time and money formatting. Every helper accepts null/undefined and returns a dash. */

const DASH = '–'

const intFmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })
const compactFmt = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 })
const pctFmt = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 })
const usd2 = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })
const usd4 = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', minimumFractionDigits: 4, maximumFractionDigits: 4 })
const dateTimeFmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const dayFmt = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
const hourFmt = new Intl.DateTimeFormat(undefined, { hour: 'numeric' })
const relFmt = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto', style: 'short' })

const ok = (n: number | null | undefined): n is number => typeof n === 'number' && Number.isFinite(n)

export const fmtInt = (n: number | null | undefined): string => (ok(n) ? intFmt.format(n) : DASH)
export const fmtCompact = (n: number | null | undefined): string => (ok(n) ? compactFmt.format(n) : DASH)

/** 0..1 → "12.5%". */
export const fmtPct = (n: number | null | undefined, empty = DASH): string => (ok(n) ? pctFmt.format(n) : empty)

export function fmtUsd(n: number | null | undefined): string {
  if (!ok(n)) return DASH
  if (n === 0) return usd2.format(0)
  return Math.abs(n) < 0.01 ? usd4.format(n) : usd2.format(n)
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (!ok(seconds) || seconds < 0) return DASH
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  if (m < 60) return s ? `${m} min ${s} s` : `${m} min`
  const h = Math.floor(m / 60)
  return `${h} h ${m % 60} min`
}

/** Unix seconds → "Oct 18, 2026, 5:09 PM". */
export const fmtDateTime = (ts: number | null | undefined): string => (ok(ts) ? dateTimeFmt.format(new Date(ts * 1000)) : DASH)
export const fmtDay = (ts: number): string => dayFmt.format(new Date(ts * 1000))
export const fmtHour = (ts: number): string => hourFmt.format(new Date(ts * 1000))

/** Unix seconds relative to `now` (unix seconds) → "5 min ago". */
export function fmtRelative(ts: number | null | undefined, now: number): string {
  if (!ok(ts)) return DASH
  const d = ts - now
  const a = Math.abs(d)
  if (a < 10) return 'just now'
  if (a < 60) return relFmt.format(Math.round(d), 'second')
  if (a < 3600) return relFmt.format(Math.round(d / 60), 'minute')
  if (a < 86400) return relFmt.format(Math.round(d / 3600), 'hour')
  return relFmt.format(Math.round(d / 86400), 'day')
}

/** "verifier/gemini-3.1-flash-lite" → "gemini-3.1-flash-lite". */
export function shortModel(model: string): string {
  const i = model.indexOf('/')
  return i >= 0 ? model.slice(i + 1) : model
}

/** Only http(s) URLs may become links. */
export function safeHref(url: unknown): string | null {
  if (typeof url !== 'string') return null
  return url.startsWith('https://') || url.startsWith('http://') ? url : null
}

export const plural = (n: number, one: string, many = `${one}s`): string => `${fmtInt(n)} ${n === 1 ? one : many}`

export function hostOf(url: string): string {
  try {
    return new URL(url).host || url
  } catch {
    return url
  }
}
