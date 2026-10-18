import { LogOut } from 'lucide-react'
import { href, type Route } from '../lib/route'

export type Connection = 'ok' | 'stale' | 'offline' | 'unknown'

interface Props {
  route: Route
  signedIn: boolean
  connection: Connection
  onSignOut: () => void
}

const CONNECTION: Record<Connection, { label: string; dot: string }> = {
  ok: { label: 'Connected', dot: 'bg-supported-line' },
  stale: { label: 'Reconnecting', dot: 'bg-likely-line' },
  offline: { label: 'Not connected', dot: 'bg-contradicted-line' },
  unknown: { label: 'Connecting', dot: 'bg-ink-400' },
}

export function Header({ route, signedIn, connection, onSignOut }: Props) {
  const current = route.page === 'run' ? 'overview' : route.page
  const link = (to: Route, label: string) => (
    <a
      href={href(to)}
      aria-current={current === to.page ? 'page' : undefined}
      className={`rounded-md px-2.5 py-1.5 text-sm font-medium ${
        current === to.page
          ? 'bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white'
          : 'text-ink-600 hover:text-ink-900 dark:text-ink-300 dark:hover:text-white'
      }`}
    >
      {label}
    </a>
  )
  const conn = CONNECTION[connection]
  return (
    <header className="border-b border-ink-200 bg-white dark:border-ink-800 dark:bg-ink-900">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 sm:px-6">
        <a href="#/" className="flex items-center gap-2 rounded-md">
          <svg viewBox="0 0 32 32" className="h-6 w-6" aria-hidden="true">
            <path d="M16 2 4 7v8c0 7.4 5.1 13.4 12 15 6.9-1.6 12-7.6 12-15V7L16 2z" className="fill-ink-900 dark:fill-ink-100" />
            <path
              d="m10.5 16 4 4 7-8"
              fill="none"
              className="stroke-white dark:stroke-ink-900"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="text-lg font-bold tracking-tight text-ink-950 dark:text-white">
            s<span className="text-action dark:text-[#8fb3f0]">AI</span>fe <span className="font-semibold text-ink-500 dark:text-ink-400">Monitor</span>
          </span>
        </a>
        <nav aria-label="Main" className="flex gap-1">
          {signedIn ? link({ page: 'overview' }, 'Overview') : null}
          {signedIn ? link({ page: 'bench' }, 'Benchmarks') : null}
          {link({ page: 'setup' }, 'Setup')}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          {signedIn ? (
            <span className="flex items-center gap-1.5 text-xs text-ink-500 dark:text-ink-400" role="status">
              <span className={`h-2 w-2 rounded-full ${conn.dot}`} aria-hidden="true" />
              {conn.label}
            </span>
          ) : null}
          {signedIn ? (
            <button type="button" className="btn" onClick={onSignOut}>
              <LogOut aria-hidden="true" className="h-4 w-4" />
              Sign out
            </button>
          ) : null}
        </div>
      </div>
    </header>
  )
}
