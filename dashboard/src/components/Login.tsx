import { KeyRound, LogIn } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { getRuns, UnauthorizedError, NotConfiguredError } from '../api'
import { Notice } from './Panel'

interface Props {
  message: string | null
  onSignedIn: (password: string) => void
  onNotConfigured: () => void
}

export function Login({ message, onSignedIn, onNotConfigured }: Props) {
  const id = useId()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!password) return
    setBusy(true)
    setError(null)
    try {
      await getRuns(password)
      onSignedIn(password)
    } catch (err) {
      if (err instanceof UnauthorizedError) setError('That password is not right. It is the DASHBOARD_PASSWORD set on Vercel.')
      else if (err instanceof NotConfiguredError) onNotConfigured()
      else setError(err instanceof Error ? err.message : 'Sign-in failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto flex max-w-sm flex-col gap-4 px-4 py-16">
      <div className="panel p-6">
        <div className="mb-4 flex items-center gap-2">
          <KeyRound aria-hidden="true" className="h-5 w-5 text-action dark:text-[#8fb3f0]" />
          <h1 className="text-lg font-semibold">Sign in</h1>
        </div>
        <p className="mb-4 text-sm text-ink-600 dark:text-ink-300">
          Enter the dashboard password to see what your sAIfe judges flagged.
        </p>
        <form onSubmit={submit} className="flex flex-col gap-3">
          {message ? <Notice tone="warning">{message}</Notice> : null}
          {error ? <Notice tone="error">{error}</Notice> : null}
          <label htmlFor={`${id}-pw`} className="text-sm font-medium">
            Password
          </label>
          <input
            id={`${id}-pw`}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-ink-300 bg-white px-3 py-2 text-sm text-ink-900 dark:border-ink-700 dark:bg-ink-950 dark:text-ink-100"
            required
          />
          <button type="submit" className="btn-primary" disabled={busy || !password}>
            <LogIn aria-hidden="true" className="h-4 w-4" />
            {busy ? 'Checking…' : 'Sign in'}
          </button>
        </form>
      </div>
      <p className="text-center text-xs text-ink-500 dark:text-ink-400">
        Not set up yet? <a href="#/setup" className="font-medium text-action underline dark:text-[#8fb3f0]">See the setup steps</a>.
      </p>
    </main>
  )
}
