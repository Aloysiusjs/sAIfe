import type { LucideIcon } from 'lucide-react'
import { Activity, BadgeCheck, CircleHelp, OctagonX, ShieldAlert, ShieldCheck, ShieldX } from 'lucide-react'
import type { Kpis } from '../lib/stats'
import { fmtInt, fmtPct } from '../lib/format'

interface TileProps {
  label: string
  value: string
  icon?: LucideIcon
  iconClass?: string
  detail?: string
}

function Tile({ label, value, icon: Icon, iconClass = 'text-ink-400', detail }: TileProps) {
  return (
    <div className="panel flex min-w-0 flex-col gap-1 p-3">
      <dt className="flex items-center gap-1.5 label-xs">
        {Icon ? <Icon aria-hidden="true" className={`h-3.5 w-3.5 ${iconClass}`} strokeWidth={2.25} /> : null}
        {label}
      </dt>
      <dd className="text-2xl font-semibold tabular-nums text-ink-950 dark:text-white">{value}</dd>
      {detail ? <dd className="truncate text-xs text-ink-500 dark:text-ink-400">{detail}</dd> : null}
    </div>
  )
}

export function KpiTiles({ k }: { k: Kpis }) {
  const share = (n: number) => (k.claims ? `${fmtPct(n / k.claims)} of ${fmtInt(k.claims)} claims` : 'no claims yet')
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
      <Tile label="Runs" value={fmtInt(k.runs)} icon={Activity} detail={k.failed ? `${fmtInt(k.failed)} failed` : 'none failed'} />
      <Tile
        label="Hallucinations caught"
        value={fmtInt(k.caught)}
        icon={OctagonX}
        iconClass="text-contradicted-line"
        detail={share(k.caught)}
      />
      <Tile label="Claims passed" value={fmtInt(k.passed)} icon={BadgeCheck} iconClass="text-supported-line" detail={share(k.passed)} />
      <Tile
        label="Unverified"
        value={fmtInt(k.unverifiable + k.likely)}
        icon={CircleHelp}
        iconClass="text-unverifiable-line"
        detail={`${fmtInt(k.unverifiable)} unverifiable · ${fmtInt(k.likely)} not evidence-backed`}
      />
      <Tile label="Verified answers" value={fmtInt(k.verified)} icon={ShieldCheck} iconClass="text-supported-line" />
      <Tile
        label="Caution / Blocked"
        value={`${fmtInt(k.caution)} / ${fmtInt(k.blocked)}`}
        icon={k.blocked ? ShieldX : ShieldAlert}
        iconClass={k.blocked ? 'text-contradicted-line' : 'text-likely-line'}
      />
      <Tile label="Running now" value={fmtInt(k.running)} icon={Activity} iconClass="text-action dark:text-[#8fb3f0]" />
    </dl>
  )
}
