import { useEffect, useId, useRef, useState } from 'react'
import type { ClaimStatus } from '../../api/_lib/core'
import type { Bucket } from '../lib/stats'
import { CHART_ORDER, CLAIM_STATUS } from '../lib/status'
import { fmtDay, fmtHour, fmtInt } from '../lib/format'

interface Props {
  buckets: Bucket[]
  unit: 'hour' | 'day'
}

const H = 160
const PAD_TOP = 8
const PAD_BOTTOM = 20
const PAD_LEFT = 28
const GAP = 2 // surface gap between stacked segments

const total = (b: Bucket) => CHART_ORDER.reduce((n, s) => n + b.counts[s], 0)

function niceMax(v: number): number {
  if (v <= 4) return 4
  const p = 10 ** Math.floor(Math.log10(v))
  for (const m of [1, 2, 4, 6, 8, 10]) if (m * p >= v) return m * p
  return 10 * p
}

/** Stacked bars of claims per bucket, by status. Hand-drawn SVG, with a hidden data table. */
export function ClaimsChart({ buckets, unit }: Props) {
  const id = useId()
  const [hover, setHover] = useState<number | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  useEffect(() => {
    const el = box.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w && w > 0) setWidth(Math.round(w))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const plotW = width - PAD_LEFT
  const plotH = H - PAD_TOP - PAD_BOTTOM
  const max = niceMax(Math.max(0, ...buckets.map(total)))
  const slot = plotW / Math.max(1, buckets.length)
  const barW = Math.max(3, Math.min(28, slot * 0.66))
  const y = (v: number) => PAD_TOP + plotH - (v / max) * plotH
  const label = (b: Bucket) => (unit === 'hour' ? fmtHour(b.start) : fmtDay(b.start))
  const labelEvery = Math.max(1, Math.ceil(buckets.length / Math.max(2, Math.floor(plotW / 56))))
  const totals: Record<ClaimStatus, number> = { SUPPORTED: 0, LIKELY: 0, UNSUPPORTED: 0, CONTRADICTED: 0, UNVERIFIABLE: 0 }
  for (const b of buckets) for (const s of CHART_ORDER) totals[s] += b.counts[s]
  const sum = CHART_ORDER.reduce((n, s) => n + totals[s], 0)
  const summary = `Claims per ${unit}, stacked by status. ${fmtInt(sum)} claims in total: ${CHART_ORDER.map(
    (s) => `${fmtInt(totals[s])} ${CLAIM_STATUS[s].label.toLowerCase()}`,
  ).join(', ')}.`
  const hb = hover !== null ? buckets[hover] : undefined

  return (
    <figure className="min-w-0">
      <div className="relative" ref={box}>
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} className="block max-w-full" role="img" aria-label={summary}>
          {[0, 0.5, 1].map((f) => (
            <g key={f}>
              <line
                x1={PAD_LEFT}
                x2={width}
                y1={y(max * f)}
                y2={y(max * f)}
                className={f === 0 ? 'stroke-ink-300 dark:stroke-ink-600' : 'stroke-ink-100 dark:stroke-ink-800'}
                strokeWidth={1}
              />
              <text x={PAD_LEFT - 6} y={y(max * f) + 3} textAnchor="end" className="fill-ink-500 text-[10px] dark:fill-ink-400">
                {fmtInt(max * f)}
              </text>
            </g>
          ))}
          {buckets.map((b, i) => {
            const cx = PAD_LEFT + slot * i + slot / 2
            let acc = 0
            const segs = CHART_ORDER.filter((s) => b.counts[s] > 0)
            return (
              <g key={b.start} opacity={hover === null || hover === i ? 1 : 0.45}>
                {segs.map((s, si) => {
                  const v = b.counts[s]
                  const top = y(acc + v)
                  const bottom = y(acc)
                  acc += v
                  const h = Math.max(1, bottom - top - (si < segs.length - 1 ? GAP : 0))
                  const isTop = si === segs.length - 1
                  return (
                    <rect
                      key={s}
                      x={cx - barW / 2}
                      y={isTop ? top : top + GAP}
                      width={barW}
                      height={isTop ? Math.max(1, bottom - top) : h}
                      rx={isTop ? Math.min(3, barW / 2) : 0}
                      className={CLAIM_STATUS[s].fill}
                    />
                  )
                })}
                {i % labelEvery === 0 ? (
                  <text x={cx} y={H - 5} textAnchor="middle" className="fill-ink-500 text-[10px] dark:fill-ink-400">
                    {label(b)}
                  </text>
                ) : null}
                {/* Hit target taller and wider than the mark. */}
                <rect
                  x={PAD_LEFT + slot * i}
                  y={PAD_TOP}
                  width={slot}
                  height={plotH}
                  fill="transparent"
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover(null)}
                />
              </g>
            )
          })}
        </svg>
        {hb ? (
          <div
            className="pointer-events-none absolute top-0 z-10 w-48 rounded-md border border-ink-200 bg-white p-2 text-xs shadow-drawer dark:border-ink-700 dark:bg-ink-900"
            style={{
              left: `clamp(0px, calc(${((PAD_LEFT + slot * (hover ?? 0) + slot / 2) / width) * 100}% - 6rem), calc(100% - 12rem))`,
            }}
            aria-hidden="true"
          >
            <p className="mb-1 font-semibold">
              {unit === 'hour' ? `${fmtDay(hb.start)}, ${fmtHour(hb.start)}` : fmtDay(hb.start)} · {fmtInt(hb.runs)} runs
            </p>
            {[...CHART_ORDER].reverse().map((s) => (
              <p key={s} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5">
                  <span className={`h-2 w-2 rounded-sm ${CLAIM_STATUS[s].swatch}`} />
                  {CLAIM_STATUS[s].label}
                </span>
                <span className="tabular-nums">{fmtInt(hb.counts[s])}</span>
              </p>
            ))}
          </div>
        ) : null}
      </div>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-600 dark:text-ink-300">
        {[...CHART_ORDER].reverse().map((s) => {
          const Icon = CLAIM_STATUS[s].icon
          return (
            <span key={s} className="flex items-center gap-1.5">
              <span className={`h-2.5 w-2.5 rounded-sm ${CLAIM_STATUS[s].swatch}`} aria-hidden="true" />
              <Icon aria-hidden="true" className="h-3 w-3" />
              {s === 'LIKELY' ? 'Likely (not evidence-backed)' : CLAIM_STATUS[s].label}
              <span className="tabular-nums text-ink-500 dark:text-ink-400">{fmtInt(totals[s])}</span>
            </span>
          )
        })}
      </figcaption>
      <table className="sr-only" aria-labelledby={`${id}-cap`}>
        <caption id={`${id}-cap`}>Claims per {unit} by status</caption>
        <thead>
          <tr>
            <th scope="col">{unit === 'hour' ? 'Hour' : 'Day'}</th>
            {CHART_ORDER.map((s) => (
              <th key={s} scope="col">
                {CLAIM_STATUS[s].label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {buckets.map((b) => (
            <tr key={b.start}>
              <th scope="row">{unit === 'hour' ? `${fmtDay(b.start)} ${fmtHour(b.start)}` : fmtDay(b.start)}</th>
              {CHART_ORDER.map((s) => (
                <td key={s}>{b.counts[s]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  )
}
