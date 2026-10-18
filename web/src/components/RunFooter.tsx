import { ShieldCheck, ShieldOff } from 'lucide-react'
import type { Report } from '../api/types'
import { STOP_CAUSE } from '../lib/status'

const fmt = new Intl.NumberFormat('en-US')

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs font-medium uppercase tracking-wide text-ink-500">{label}</dt>
      <dd className="mt-0.5 text-sm font-semibold tabular-nums text-ink-900">
        {value}
        {sub ? <span className="ml-1 font-normal text-ink-500">{sub}</span> : null}
      </dd>
    </div>
  )
}

export function RunFooter({ report }: { report: Report }) {
  const u = report.usage
  return (
    <footer aria-label="Run summary" className="rounded-lg border border-ink-200 bg-white px-4 py-3 shadow-panel sm:px-5">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Passes" value={String(report.passes.length)} sub={`preset ${report.preset}`} />
        <Stat label="Tokens" value={fmt.format(u.input_tokens + u.output_tokens)} sub={`${u.calls} calls`} />
        <Stat label="Cost" value={`$${u.cost_usd.toFixed(3)}`} sub="estimate" />
        <Stat label="Wall time" value={`${u.wall_time_s.toFixed(1)} s`} />
        <Stat label="Stopped" value={STOP_CAUSE[report.stop_cause]} />
        <div className="min-w-0">
          <dt className="text-2xs font-medium uppercase tracking-wide text-ink-500">Verifier diversity</dt>
          <dd className={`mt-0.5 flex items-center gap-1 text-sm font-semibold ${report.verifier_diversity ? 'text-supported-fg' : 'text-likely-fg'}`}>
            {report.verifier_diversity ? (
              <ShieldCheck aria-hidden="true" className="h-4 w-4" />
            ) : (
              <ShieldOff aria-hidden="true" className="h-4 w-4" />
            )}
            {report.verifier_diversity ? 'Cross-provider' : 'Not cross-provider'}
          </dd>
        </div>
      </dl>
      {report.fallbacks.length > 0 ? (
        <ul className="mt-3 space-y-1 border-t border-ink-100 pt-2 text-xs text-ink-700">
          {report.fallbacks.map((f, i) => (
            <li key={i}>
              <span className="font-semibold">Fallback:</span> {f.role.replace('_', ' ')} moved from{' '}
              <code className="font-mono">{f.from_model}</code> to <code className="font-mono">{f.to_model}</code> ({f.reason})
            </li>
          ))}
        </ul>
      ) : null}
      {Object.keys(u.by_model).length > 0 ? (
        <details className="mt-2 text-xs text-ink-700">
          <summary className="cursor-pointer text-ink-600">Tokens by model</summary>
          <ul className="mt-1 space-y-0.5 tabular-nums">
            {Object.entries(u.by_model).map(([m, t]) => (
              <li key={m}>
                <code className="font-mono">{m}</code>: {fmt.format(t.input_tokens)} in · {fmt.format(t.output_tokens)} out
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </footer>
  )
}
