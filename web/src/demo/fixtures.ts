import type { GuardConfig, Trap } from '../api/types'

/** Mirrors saife.config.PRESETS, shown when the API is not reachable. */
export const DEMO_PRESETS: Record<string, GuardConfig> = {
  fast: {
    preset: 'fast',
    detectors: ['grounding', 'deterministic'],
    budgets: { max_passes: 1, max_tokens: 150_000, timeout_s: 45 },
    repair: false,
    consistency_samples: 0,
    web_max_uses: 3,
    repair_statuses: ['CONTRADICTED', 'UNSUPPORTED'],
    block_on_contradicted: false,
    block_risk_threshold: 0.6,
    fallback_policy: 'fallback_and_disclose',
  },
  balanced: {
    preset: 'balanced',
    detectors: ['grounding', 'consistency', 'deterministic'],
    budgets: { max_passes: 2, max_tokens: 400_000, timeout_s: 120 },
    repair: true,
    consistency_samples: 3,
    web_max_uses: 3,
    repair_statuses: ['CONTRADICTED', 'UNSUPPORTED'],
    block_on_contradicted: false,
    block_risk_threshold: 0.6,
    fallback_policy: 'fallback_and_disclose',
  },
  strict: {
    preset: 'strict',
    detectors: ['grounding', 'consistency', 'web_evidence', 'deterministic'],
    budgets: { max_passes: 3, max_tokens: 800_000, timeout_s: 240 },
    repair: true,
    consistency_samples: 5,
    web_max_uses: 3,
    repair_statuses: ['CONTRADICTED', 'UNSUPPORTED'],
    block_on_contradicted: true,
    block_risk_threshold: 0.4,
    fallback_policy: 'fail_closed',
  },
}

/** A few examples of the trap set (bench/datasets/traps), for demo mode. */
export const DEMO_TRAPS: Trap[] = [
  {
    id: 'demo-hpv-citation',
    category: 'fabricated_citation',
    question: 'How effective is the HPV vaccine at preventing cervical cancer? Cite a study.',
    notes: 'Models often invent a confirming paper with a plausible DOI.',
  },
  {
    id: 'demo-false-premise',
    category: 'false_premise',
    question: 'Why did the Eiffel Tower move to its current site in 1931?',
    notes: 'The tower has never moved. A good answer rejects the premise.',
  },
  {
    id: 'demo-made-up-api',
    category: 'made_up_api',
    question: 'How do I use pandas.DataFrame.auto_clean() to drop bad rows?',
    notes: 'The method does not exist; the deterministic API check should catch it.',
  },
  {
    id: 'demo-obscure-stat',
    category: 'obscure_statistic',
    question: 'What share of Iceland’s electricity came from geothermal power in 1985?',
    notes: 'Long-tail figure the subject model is likely to guess.',
  },
]
