import { CLAIM_STATUS_ORDER } from '../lib/status'
import { StatusBadge } from './StatusBadge'

export function EmptyState({ demo }: { demo: boolean }) {
  return (
    <div className="rounded-lg border border-dashed border-ink-300 bg-white/60 px-5 py-8 sm:px-8">
      <h2 className="text-lg font-semibold text-ink-900">Every claim, checked before you trust it</h2>
      <p className="mt-2 max-w-[62ch] text-sm leading-relaxed text-ink-700">
        sAIfe splits an answer into claims, checks each one against your sources and with independent detectors, repairs
        what it can prove wrong, and tells you plainly what it could not verify.{' '}
        {demo ? 'Press “Replay the recorded run” to watch a full check.' : 'Ask a question or paste an answer to start.'}
      </p>
      <ul className="mt-5 flex flex-wrap gap-2" aria-label="Claim statuses">
        {CLAIM_STATUS_ORDER.map((s) => (
          <li key={s}>
            <StatusBadge status={s} size="md" />
          </li>
        ))}
      </ul>
    </div>
  )
}
