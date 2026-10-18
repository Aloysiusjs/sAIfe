import type { ReactNode } from 'react'
import { DETECTOR } from '../../lib/status'
import type { DetectorName } from '../../api/types'
import { Delta, pct, presetLabel, score, SilentFailures } from './format'
import type { AblationGroup, VerifierPair } from './select'
import type { SeededRun, TrapRun } from './types'

const th = 'px-3 py-2 text-left text-xs font-semibold text-ink-700 first:pl-0'
const td = 'px-3 py-2 align-top tabular-nums first:pl-0'

function Table({ caption, head, children }: { caption: string; head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[34rem] border-collapse text-sm text-ink-900">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-ink-200">
            {head.map((h) => (
              <th key={h} scope="col" className={th}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">{children}</tbody>
      </table>
    </div>
  )
}

const detectorLabel = (d: string) =>
  d
    .split(' + ')
    .map((x) => DETECTOR[x as DetectorName]?.label ?? x)
    .join(' + ')

export function DetectionTable({ runs }: { runs: SeededRun[] }) {
  return (
    <Table
      caption="Claim-level detection per preset"
      head={['Preset', 'Precision', 'Recall', 'F1', 'False alarms', 'Errors caught', 'Silent failures', 'Run']}
    >
      {runs.map((r) => {
        const m = r.metrics
        return (
          <tr key={r.name}>
            <th scope="row" className={`${td} text-left font-semibold`}>
              {presetLabel(r.meta.preset)}
            </th>
            <td className={td}>{score(m.precision)}</td>
            <td className={td}>{score(m.recall)}</td>
            <td className={td}>{score(m.f1)}</td>
            <td className={td}>
              {pct(m.false_alarm_rate)}
              <span className="block text-2xs text-ink-500">of {m.clean_items} clean answers</span>
            </td>
            <td className={td}>
              {m.errors_detected} / {m.errors_total}
            </td>
            <td className={td}>
              <SilentFailures n={m.silent_failures} />
            </td>
            <td className={`${td} text-xs text-ink-600`}>
              {r.meta.date} · {r.meta.n_items} items
            </td>
          </tr>
        )
      })}
    </Table>
  )
}

export function AblationTable({ group }: { group: AblationGroup }) {
  const f = group.full.metrics
  return (
    <Table
      caption={`Detector ablation for the ${presetLabel(group.preset)} preset, compared with the full preset`}
      head={['Detector removed', 'F1', 'Change in F1', 'Recall', 'Change in recall', 'Residual (with sAIfe)', 'Change']}
    >
      <tr className="bg-ink-50">
        <th scope="row" className={`${td} text-left font-semibold`}>
          None (full {presetLabel(group.preset)})
        </th>
        <td className={td}>{score(f.f1)}</td>
        <td className={`${td} text-ink-500`}>reference</td>
        <td className={td}>{score(f.recall)}</td>
        <td className={`${td} text-ink-500`}>reference</td>
        <td className={td}>{pct(f.residual_guarded)}</td>
        <td className={`${td} text-ink-500`}>reference</td>
      </tr>
      {group.rows.map(({ ablated, run }) => {
        const m = run.metrics
        return (
          <tr key={run.name}>
            <th scope="row" className={`${td} text-left font-semibold`}>
              {detectorLabel(ablated)}
            </th>
            <td className={td}>{score(m.f1)}</td>
            <td className={td}>
              <Delta value={m.f1} base={f.f1} higherIsBetter kind="score" />
            </td>
            <td className={td}>{score(m.recall)}</td>
            <td className={td}>
              <Delta value={m.recall} base={f.recall} higherIsBetter kind="score" />
            </td>
            <td className={td}>{pct(m.residual_guarded)}</td>
            <td className={td}>
              <Delta value={m.residual_guarded} base={f.residual_guarded} higherIsBetter={false} kind="pct" />
            </td>
          </tr>
        )
      })}
    </Table>
  )
}

export function VerifierTable({ pairs }: { pairs: VerifierPair[] }) {
  return (
    <Table
      caption="Cross-provider verifier compared with every judge on the same Claude model"
      head={['Preset', 'Verifier', 'Precision', 'Recall', 'F1', 'Residual (with sAIfe)', 'F1 vs same-model']}
    >
      {pairs.flatMap(({ preset, cross, same }) =>
        [
          { label: 'Cross-provider', run: cross, base: same },
          { label: 'Same model (Claude)', run: same, base: null },
        ].map(({ label, run, base }, i) => (
          <tr key={run.name}>
            {i === 0 ? (
              <th scope="rowgroup" rowSpan={2} className={`${td} text-left font-semibold`}>
                {presetLabel(preset)}
              </th>
            ) : null}
            <td className={td}>{label}</td>
            <td className={td}>{score(run.metrics.precision)}</td>
            <td className={td}>{score(run.metrics.recall)}</td>
            <td className={td}>{score(run.metrics.f1)}</td>
            <td className={td}>{pct(run.metrics.residual_guarded)}</td>
            <td className={td}>
              {base ? (
                <Delta value={run.metrics.f1} base={base.metrics.f1} higherIsBetter kind="score" />
              ) : (
                <span className="text-ink-500">reference</span>
              )}
            </td>
          </tr>
        )),
      )}
    </Table>
  )
}

export function TrapTable({ runs }: { runs: TrapRun[] }) {
  return (
    <Table caption="Trap prompts flagged per preset" head={['Preset', 'Flagged', 'Target', 'Silent failures', 'Run']}>
      {runs.map((r) => (
        <tr key={r.name}>
          <th scope="row" className={`${td} text-left font-semibold`}>
            {presetLabel(r.meta.preset)}
          </th>
          <td className={td}>{pct(r.metrics.flag_rate)}</td>
          <td className={`${td} text-ink-600`}>
            {r.metrics.flag_rate !== null && r.metrics.flag_rate >= 0.5 ? 'at least 50%: met' : 'at least 50%: not met'}
          </td>
          <td className={td}>
            <SilentFailures n={r.metrics.silent_failures} />
          </td>
          <td className={`${td} text-xs text-ink-600`}>
            {r.meta.date} · {r.meta.n_items} prompts
          </td>
        </tr>
      ))}
    </Table>
  )
}
