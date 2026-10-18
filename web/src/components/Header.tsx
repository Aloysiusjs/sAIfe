import type { Route } from '../lib/route'

interface Props {
  route: Route
  onNavigate: (to: Route) => void
  demo: boolean
  demoReason: 'requested' | 'offline' | null
}

export function Header({ route, onNavigate, demo, demoReason }: Props) {
  const link = (to: Route, label: string) => (
    <a
      href={to === 'results' ? '/results' : '/'}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
        e.preventDefault()
        onNavigate(to)
      }}
      aria-current={route === to ? 'page' : undefined}
      className={`rounded-md px-2.5 py-1.5 text-sm font-medium ${route === to ? 'bg-ink-100 text-ink-900' : 'text-ink-600 hover:text-ink-900'}`}
    >
      {label}
    </a>
  )
  return (
    <header className="border-b border-ink-200 bg-white">
      <div className="mx-auto flex max-w-[90rem] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
        <p className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-6 w-6" />
          <span className="text-lg font-bold tracking-tight text-ink-950">
            s<span className="text-action">AI</span>fe
          </span>
          <span className="hidden text-sm text-ink-500 sm:inline">Multi-pass hallucination guard</span>
        </p>
        <nav aria-label="Main" className="flex gap-1">
          {link('check', 'Check')}
          {link('results', 'Benchmark')}
        </nav>
        {demo ? (
          <p className="ml-auto rounded-md border border-likely-line bg-likely-bg px-2.5 py-1 text-xs font-medium text-likely-fg">
            {demoReason === 'offline'
              ? 'Demo mode: the sAIfe server is not reachable, so a recorded run is replayed.'
              : 'Demo mode: replaying a recorded run.'}
          </p>
        ) : null}
      </div>
    </header>
  )
}
