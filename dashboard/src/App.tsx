import { useCallback, useState } from 'react'
import { getHealth, NotConfiguredError, UnauthorizedError } from './api'
import { Bench } from './components/Bench'
import { Header, type Connection } from './components/Header'
import { Login } from './components/Login'
import { Overview } from './components/Overview'
import { Spinner } from './components/Panel'
import { RunDetail } from './components/RunDetail'
import { Setup } from './components/Setup'
import { navigate, useHashRoute } from './lib/route'
import { clearPassword, loadPassword, savePassword } from './lib/storage'
import { usePolling } from './lib/usePolling'

export function App() {
  const route = useHashRoute()
  const [password, setPassword] = useState<string | null>(() => loadPassword())
  const [loginMessage, setLoginMessage] = useState<string | null>(null)
  const [connection, setConnection] = useState<Connection>('unknown')
  const health = usePolling('health', (signal) => getHealth(signal), null)

  const signOut = useCallback((message: string | null = null) => {
    clearPassword()
    setPassword(null)
    setLoginMessage(message)
    setConnection('unknown')
  }, [])

  /** Handles auth and configuration errors for every page. Returns true when handled. */
  const onApiError = useCallback(
    (e: unknown): boolean => {
      if (e instanceof UnauthorizedError) {
        signOut('The password was not accepted. It may have changed; sign in again.')
        return true
      }
      if (e instanceof NotConfiguredError) {
        health.refresh()
        navigate({ page: 'setup' })
        return true
      }
      return false
    },
    [signOut, health.refresh],
  )

  const healthError = health.error instanceof Error ? health.error.message : health.error ? 'unknown error' : null
  const needsSetup = route.page === 'setup' || (health.data !== null && !health.data.ok) || (!health.data && !!health.error)
  const signedIn = password !== null

  let body
  if (needsSetup) {
    body = <Setup health={health.data} error={healthError} loading={health.loading} onRecheck={health.refresh} signedIn={signedIn} />
  } else if (!health.data) {
    body = <Spinner label="Checking the dashboard" />
  } else if (!password) {
    body = (
      <Login
        message={loginMessage}
        onSignedIn={(pw) => {
          savePassword(pw)
          setPassword(pw)
          setLoginMessage(null)
        }}
        onNotConfigured={() => {
          health.refresh()
          navigate({ page: 'setup' })
        }}
      />
    )
  } else if (route.page === 'run') {
    body = <RunDetail key={route.id} id={route.id} password={password} onApiError={onApiError} onConnection={setConnection} />
  } else if (route.page === 'bench') {
    body = <Bench password={password} onApiError={onApiError} onConnection={setConnection} />
  } else {
    body = <Overview password={password} onApiError={onApiError} onConnection={setConnection} />
  }

  return (
    <div className="min-h-screen overflow-x-hidden">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm dark:focus:bg-ink-900"
        onClick={(e) => {
          e.preventDefault()
          document.getElementById('main')?.focus()
        }}
      >
        Skip to content
      </a>
      <Header route={route} signedIn={signedIn} connection={connection} onSignOut={() => signOut(null)} />
      <div id="main" tabIndex={-1} className="outline-none">
        {body}
      </div>
    </div>
  )
}
