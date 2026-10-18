import type { Claim, RepairRecord, Span } from '../api/types'

/**
 * Locate a claim's anchor in `text`. Spans index into the draft the claim was
 * extracted from; when the text differs (a carried claim, or a final answer that
 * was repaired after its last pass) the anchor is searched for instead.
 */
export function resolveSpan(text: string, span: Span | null, anchor: string): Span | null {
  if (span) {
    const [s, e] = span
    if (s >= 0 && e <= text.length && s < e && text.slice(s, e) === anchor) return span
  }
  if (!anchor) return null
  const at = text.indexOf(anchor)
  return at >= 0 ? [at, at + anchor.length] : null
}

export interface Mark {
  start: number
  end: number
  claimId: string
}

export interface Segment {
  text: string
  claimId: string | null
}

/** Split text into plain and claim segments. Overlapping or unlocatable claims are returned separately. */
export function segmentText(text: string, claims: Claim[]): { segments: Segment[]; unplaced: Claim[] } {
  const located: Mark[] = []
  const unplaced: Claim[] = []
  for (const c of claims) {
    const span = resolveSpan(text, c.span, c.anchor)
    if (span) located.push({ start: span[0], end: span[1], claimId: c.id })
    else unplaced.push(c)
  }
  located.sort((a, b) => a.start - b.start || b.end - a.end)

  const segments: Segment[] = []
  let cursor = 0
  for (const m of located) {
    if (m.start < cursor) {
      const claim = claims.find((c) => c.id === m.claimId)
      if (claim) unplaced.push(claim)
      continue
    }
    if (m.start > cursor) segments.push({ text: text.slice(cursor, m.start), claimId: null })
    segments.push({ text: text.slice(m.start, m.end), claimId: m.claimId })
    cursor = m.end
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), claimId: null })
  return { segments, unplaced }
}

export interface DiffPiece {
  kind: 'same' | 'edit'
  text: string
  repair?: RepairRecord
  number?: number
}

/** Interleave a pre-repair draft with its repairs. Repairs whose `before` can't be found are returned apart. */
export function diffPieces(
  draft: string,
  repairs: RepairRecord[],
  numberOf: (r: RepairRecord) => number,
): { pieces: DiffPiece[]; unplaced: RepairRecord[] } {
  const placed: { start: number; end: number; repair: RepairRecord }[] = []
  const unplaced: RepairRecord[] = []
  for (const r of repairs) {
    const span = resolveSpan(draft, r.span, r.before)
    if (span) placed.push({ start: span[0], end: span[1], repair: r })
    else unplaced.push(r)
  }
  placed.sort((a, b) => a.start - b.start)
  const pieces: DiffPiece[] = []
  let cursor = 0
  for (const p of placed) {
    if (p.start < cursor) {
      unplaced.push(p.repair)
      continue
    }
    if (p.start > cursor) pieces.push({ kind: 'same', text: draft.slice(cursor, p.start) })
    pieces.push({ kind: 'edit', text: draft.slice(p.start, p.end), repair: p.repair, number: numberOf(p.repair) })
    cursor = p.end
  }
  if (cursor < draft.length) pieces.push({ kind: 'same', text: draft.slice(cursor) })
  return { pieces, unplaced }
}
