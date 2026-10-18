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
import type { AnswerStatus, ClaimStatus, DetectorName, RepairOp, Severity, StopCause, Verdict } from '../api/types'

export interface StatusMeta {
  label: string
  icon: LucideIcon
  /** One line for the legend and drawer: what this status means for the reader. */
  meaning: string
  text: string
  tint: string
  /** Classes for a highlighted span in running text. Underline style varies too, not just hue. */
  mark: string
  border: string
}

export const CLAIM_STATUS: Record<ClaimStatus, StatusMeta> = {
  SUPPORTED: {
    label: 'Supported',
    icon: BadgeCheck,
    meaning: 'A quote or computed check, confirmed by code, backs this claim.',
    text: 'text-supported-fg',
    tint: 'bg-supported-bg',
    mark: 'bg-supported-bg decoration-supported-line decoration-solid',
    border: 'border-supported-line',
  },
  LIKELY: {
    label: 'Likely',
    icon: CircleDashed,
    meaning: 'Resampled answers agree, but no source evidence was found. Not verified.',
    text: 'text-likely-fg',
    tint: 'bg-likely-bg',
    mark: 'bg-likely-bg decoration-likely-line decoration-dashed',
    border: 'border-likely-line',
  },
  UNSUPPORTED: {
    label: 'Unsupported',
    icon: TriangleAlert,
    meaning: 'The checks ran and found nothing either way.',
    text: 'text-unsupported-fg',
    tint: 'bg-unsupported-bg',
    mark: 'bg-unsupported-bg decoration-unsupported-line decoration-dotted',
    border: 'border-unsupported-line',
  },
  CONTRADICTED: {
    label: 'Contradicted',
    icon: OctagonX,
    meaning: 'Confirmed evidence says this claim is wrong.',
    text: 'text-contradicted-fg',
    tint: 'bg-contradicted-bg',
    mark: 'bg-contradicted-bg decoration-contradicted-line decoration-wavy',
    border: 'border-contradicted-line',
  },
  UNVERIFIABLE: {
    label: 'Unverifiable',
    icon: CircleHelp,
    meaning: 'The checks failed or could not apply. Never treated as verified.',
    text: 'text-unverifiable-fg',
    tint: 'bg-unverifiable-bg',
    mark: 'bg-unverifiable-bg decoration-unverifiable-line decoration-double',
    border: 'border-unverifiable-line',
  },
}

export const PENDING_META = {
  label: 'Checking',
  icon: LoaderCircle,
  text: 'text-ink-600',
  tint: 'bg-ink-50',
  mark: 'bg-ink-50 decoration-ink-400 decoration-dotted',
  border: 'border-ink-300',
}

export const CLAIM_STATUS_ORDER: ClaimStatus[] = ['CONTRADICTED', 'UNSUPPORTED', 'UNVERIFIABLE', 'LIKELY', 'SUPPORTED']

export const ANSWER_STATUS: Record<
  AnswerStatus,
  { label: string; icon: LucideIcon; headline: string; text: string; tint: string; border: string }
> = {
  VERIFIED: {
    label: 'Verified',
    icon: ShieldCheck,
    headline: 'Every checked claim is backed by confirmed evidence.',
    text: 'text-supported-fg',
    tint: 'bg-supported-bg',
    border: 'border-supported-line',
  },
  CAUTION: {
    label: 'Caution',
    icon: ShieldAlert,
    headline: 'Some claims could not be verified. They are marked below.',
    text: 'text-likely-fg',
    tint: 'bg-likely-bg',
    border: 'border-likely-line',
  },
  BLOCKED: {
    label: 'Blocked',
    icon: ShieldX,
    headline: 'The answer was withheld: contradicted or high-risk claims survived repair.',
    text: 'text-contradicted-fg',
    tint: 'bg-contradicted-bg',
    border: 'border-contradicted-line',
  },
}

export const SEVERITY: Record<Severity, { label: string; icon: LucideIcon; text: string }> = {
  high: { label: 'High', icon: OctagonX, text: 'text-contradicted-fg' },
  warning: { label: 'Warning', icon: TriangleAlert, text: 'text-likely-fg' },
  info: { label: 'Info', icon: Info, text: 'text-ink-600' },
}

export const VERDICT: Record<Verdict, { label: string; status: ClaimStatus }> = {
  SUPPORTED: { label: 'Supported', status: 'SUPPORTED' },
  CONTRADICTED: { label: 'Contradicted', status: 'CONTRADICTED' },
  NOT_FOUND: { label: 'Not found', status: 'UNSUPPORTED' },
  UNVERIFIABLE: { label: 'Unverifiable', status: 'UNVERIFIABLE' },
}

export const DETECTOR: Record<DetectorName, { label: string; blurb: string }> = {
  grounding: { label: 'Grounding', blurb: 'Judge checks the claim against your sources.' },
  consistency: { label: 'Consistency', blurb: 'Resampled answers are compared for agreement.' },
  web_evidence: { label: 'Web evidence', blurb: 'Searches the web and judges what it finds.' },
  deterministic: { label: 'Deterministic', blurb: 'Code checks: DOIs and URLs, arithmetic, dates, APIs.' },
}

export const REPAIR_OP: Record<RepairOp, { label: string; verb: string }> = {
  correct: { label: 'Corrected', verb: 'Replaced with text backed by verified evidence' },
  hedge: { label: 'Hedged', verb: 'Softened to state the uncertainty' },
  remove: { label: 'Removed', verb: 'Deleted from the answer' },
}

export const STOP_CAUSE: Record<StopCause, string> = {
  resolved: 'No high-risk claims left',
  max_passes: 'Reached the pass limit',
  token_budget: 'Reached the token budget',
  timeout: 'Reached the time limit',
  not_converging: 'Stopped: repairs were not converging',
  repair_disabled: 'This preset does not repair',
  repair_failed: 'Repair failed',
  decompose_failed: 'Claim extraction failed: nothing could be checked',
}
