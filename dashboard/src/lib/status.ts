import type { LucideIcon } from 'lucide-react'
import {
  BadgeCheck,
  CircleDashed,
  CircleHelp,
  Info,
  LoaderCircle,
  OctagonX,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  TriangleAlert,
} from 'lucide-react'
import type { AnswerStatus, ClaimStatus, Severity } from '../../api/_lib/core'

/*
 * Adapted from web/src/lib/status.ts. Status is never colour alone (N5): every
 * entry has an icon and a label. Dark mode swaps to a translucent tint of the
 * status `line` hue with the light `bg` hue as text, which stays readable on ink-900.
 */

export interface StatusMeta {
  label: string
  icon: LucideIcon
  meaning: string
  text: string
  tint: string
  border: string
  /** SVG fill for chart marks. */
  fill: string
  /** Swatch background for legends. */
  swatch: string
}

export const CLAIM_STATUS: Record<ClaimStatus, StatusMeta> = {
  SUPPORTED: {
    label: 'Supported',
    icon: BadgeCheck,
    meaning: 'A quote or computed check, confirmed by code, backs this claim.',
    text: 'text-supported-fg dark:text-supported-bg',
    tint: 'bg-supported-bg dark:bg-supported-line/25',
    border: 'border-supported-line',
    fill: 'fill-supported-line',
    swatch: 'bg-supported-line',
  },
  LIKELY: {
    label: 'Likely',
    icon: CircleDashed,
    meaning: 'Resampled answers agree, but no source evidence was found. Not evidence-backed.',
    text: 'text-likely-fg dark:text-likely-bg',
    tint: 'bg-likely-bg dark:bg-likely-line/25',
    border: 'border-likely-line',
    fill: 'fill-likely-line',
    swatch: 'bg-likely-line',
  },
  UNSUPPORTED: {
    label: 'Unsupported',
    icon: TriangleAlert,
    meaning: 'The checks ran and found nothing either way.',
    text: 'text-unsupported-fg dark:text-unsupported-bg',
    tint: 'bg-unsupported-bg dark:bg-unsupported-line/25',
    border: 'border-unsupported-line',
    fill: 'fill-unsupported-line',
    swatch: 'bg-unsupported-line',
  },
  CONTRADICTED: {
    label: 'Contradicted',
    icon: OctagonX,
    meaning: 'Confirmed evidence says this claim is wrong.',
    text: 'text-contradicted-fg dark:text-contradicted-bg',
    tint: 'bg-contradicted-bg dark:bg-contradicted-line/25',
    border: 'border-contradicted-line',
    fill: 'fill-contradicted-line',
    swatch: 'bg-contradicted-line',
  },
  UNVERIFIABLE: {
    label: 'Unverifiable',
    icon: CircleHelp,
    meaning: 'The checks failed or could not apply. Never treated as verified.',
    text: 'text-unverifiable-fg dark:text-ink-200',
    tint: 'bg-unverifiable-bg dark:bg-unverifiable-line/30',
    border: 'border-unverifiable-line',
    fill: 'fill-unverifiable-line',
    swatch: 'bg-unverifiable-line',
  },
}

export const PENDING_META = {
  label: 'Checking',
  icon: LoaderCircle,
  text: 'text-ink-600 dark:text-ink-300',
  tint: 'bg-ink-100 dark:bg-ink-800',
}

/** Flagged first. */
export const CLAIM_STATUS_ORDER: ClaimStatus[] = ['CONTRADICTED', 'UNSUPPORTED', 'UNVERIFIABLE', 'LIKELY', 'SUPPORTED']
/** Bottom-to-top stacking order in the chart: good at the base, worst on top. */
export const CHART_ORDER: ClaimStatus[] = ['SUPPORTED', 'LIKELY', 'UNVERIFIABLE', 'UNSUPPORTED', 'CONTRADICTED']

export const ANSWER_STATUS: Record<AnswerStatus, { label: string; icon: LucideIcon; text: string; tint: string; border: string }> = {
  VERIFIED: {
    label: 'Verified',
    icon: ShieldCheck,
    text: 'text-supported-fg dark:text-supported-bg',
    tint: 'bg-supported-bg dark:bg-supported-line/25',
    border: 'border-supported-line',
  },
  CAUTION: {
    label: 'Caution',
    icon: ShieldAlert,
    text: 'text-likely-fg dark:text-likely-bg',
    tint: 'bg-likely-bg dark:bg-likely-line/25',
    border: 'border-likely-line',
  },
  BLOCKED: {
    label: 'Blocked',
    icon: ShieldX,
    text: 'text-contradicted-fg dark:text-contradicted-bg',
    tint: 'bg-contradicted-bg dark:bg-contradicted-line/25',
    border: 'border-contradicted-line',
  },
}

export const SEVERITY: Record<Severity, { label: string; icon: LucideIcon; text: string }> = {
  high: { label: 'High', icon: OctagonX, text: 'text-contradicted-fg dark:text-contradicted-bg' },
  warning: { label: 'Warning', icon: TriangleAlert, text: 'text-likely-fg dark:text-likely-bg' },
  info: { label: 'Info', icon: Info, text: 'text-ink-600 dark:text-ink-300' },
}

export const VERDICT: Record<string, { label: string; status: ClaimStatus }> = {
  SUPPORTED: { label: 'Supported', status: 'SUPPORTED' },
  CONTRADICTED: { label: 'Contradicted', status: 'CONTRADICTED' },
  NOT_FOUND: { label: 'Not found', status: 'UNSUPPORTED' },
  UNVERIFIABLE: { label: 'Unverifiable', status: 'UNVERIFIABLE' },
}

export const DETECTOR: Record<string, string> = {
  grounding: 'Grounding',
  consistency: 'Consistency',
  web_evidence: 'Web evidence',
  deterministic: 'Deterministic',
}

export const REPAIR_OP: Record<string, { label: string; verb: string }> = {
  correct: { label: 'Corrected', verb: 'Replaced with text backed by verified evidence' },
  hedge: { label: 'Hedged', verb: 'Softened to state the uncertainty' },
  remove: { label: 'Removed', verb: 'Deleted from the answer' },
}

export const STOP_CAUSE: Record<string, string> = {
  resolved: 'No high-risk claims left',
  max_passes: 'Reached the pass limit',
  token_budget: 'Reached the token budget',
  timeout: 'Reached the time limit',
  not_converging: 'Repairs were not converging',
  repair_disabled: 'This preset does not repair',
  repair_failed: 'Repair failed',
  decompose_failed: 'Claim extraction failed',
}

export const isClaimStatus = (v: unknown): v is ClaimStatus =>
  v === 'SUPPORTED' || v === 'LIKELY' || v === 'UNSUPPORTED' || v === 'CONTRADICTED' || v === 'UNVERIFIABLE'
export const isAnswerStatus = (v: unknown): v is AnswerStatus => v === 'VERIFIED' || v === 'CAUTION' || v === 'BLOCKED'
export const isSeverity = (v: unknown): v is Severity => v === 'info' || v === 'warning' || v === 'high'
