import type { ReactNode } from 'react'

interface Props {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  id?: string
}

export function Panel({ title, actions, children, className = '', bodyClassName = 'p-4', id }: Props) {
  return (
    <section className={`panel min-w-0 ${className}`} aria-labelledby={title && id ? `${id}-title` : undefined}>
      {title || actions ? (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-100 px-4 py-2.5 dark:border-ink-800">
          {title ? (
            <h2 id={id ? `${id}-title` : undefined} className="text-sm font-semibold text-ink-900 dark:text-ink-100">
              {title}
            </h2>
          ) : null}
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className={bodyClassName}>{children}</div>
    </section>
  )
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      {icon ? <div className="text-ink-400">{icon}</div> : null}
      <p className="font-semibold text-ink-800 dark:text-ink-100">{title}</p>
      {children ? <div className="max-w-md text-sm text-ink-600 dark:text-ink-300">{children}</div> : null}
    </div>
  )
}

export function Notice({ tone = 'warning', children }: { tone?: 'warning' | 'error' | 'info'; children: ReactNode }) {
  const cls =
    tone === 'error'
      ? 'border-contradicted-line bg-contradicted-bg text-contradicted-fg dark:bg-contradicted-line/20 dark:text-contradicted-bg'
      : tone === 'warning'
        ? 'border-likely-line bg-likely-bg text-likely-fg dark:bg-likely-line/20 dark:text-likely-bg'
        : 'border-ink-200 bg-ink-50 text-ink-700 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-200'
  return (
    <div role={tone === 'info' ? undefined : 'alert'} className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${cls}`}>
      {children}
    </div>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-ink-500 dark:text-ink-400" role="status">
      <span
        className="h-4 w-4 rounded-full border-2 border-ink-300 border-t-action motion-safe:animate-spin dark:border-ink-700 dark:border-t-[#8fb3f0]"
        aria-hidden="true"
      />
      {label}
    </div>
  )
}
