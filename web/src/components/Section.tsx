import type { ReactNode } from 'react'

interface Props {
  id: string
  title: string
  aside?: ReactNode
  children: ReactNode
  className?: string
}

export function Section({ id, title, aside, children, className = '' }: Props) {
  return (
    <section aria-labelledby={id} className={`rounded-lg border border-ink-200 bg-white shadow-panel ${className}`}>
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-ink-100 px-4 py-3 sm:px-5">
        <h2 id={id} className="text-sm font-semibold text-ink-900">
          {title}
        </h2>
        {aside ? <div className="text-xs text-ink-500">{aside}</div> : null}
      </header>
      <div className="px-4 py-4 sm:px-5">{children}</div>
    </section>
  )
}
