import { ArrowRight } from 'lucide-react'
import type { Trap } from '../../api/types'

interface Props {
  traps: Trap[] | null
  error: string | null
  onPick: (trap: Trap) => void
}

const label = (category: string) => category.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())

export function TrapList({ traps, error, onPick }: Props) {
  if (error) return <p className="text-sm text-contradicted-fg">{error}</p>
  if (!traps) return <p className="text-sm text-ink-600">Loading trap questions…</p>
  if (traps.length === 0) return <p className="text-sm text-ink-600">No trap questions are available on this server.</p>

  const groups = new Map<string, Trap[]>()
  for (const t of traps) groups.set(t.category, [...(groups.get(t.category) ?? []), t])

  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-600">
        Prompts that tend to make models hallucinate. Pick one to load it into the Generate tab.
      </p>
      {[...groups].map(([category, items]) => (
        <div key={category}>
          <h3 className="text-xs font-semibold text-ink-700">{label(category)}</h3>
          <ul className="mt-1.5 space-y-1.5">
            {items.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => onPick(t)}
                  className="group flex w-full items-start gap-2 rounded-md border border-ink-200 bg-white px-3 py-2 text-left hover:border-ink-400 hover:bg-ink-50"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-ink-900">{t.question}</span>
                    {t.notes ? <span className="mt-0.5 block text-xs text-ink-500">{t.notes}</span> : null}
                  </span>
                  <ArrowRight aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-ink-400 group-hover:text-ink-800" />
                  <span className="sr-only">Use this question</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}
