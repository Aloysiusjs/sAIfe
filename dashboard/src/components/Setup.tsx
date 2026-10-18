import { CheckCircle2, CircleAlert, Copy, Check, RefreshCw } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import type { Health } from '../api'
import { Notice, Panel, Spinner } from './Panel'

interface Props {
  health: Health | null
  error: string | null
  loading: boolean
  onRecheck: () => void
  signedIn: boolean
}

function CopyField({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="flex min-w-0 items-stretch gap-1">
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-pre rounded-md border border-ink-200 bg-ink-50 px-2 py-1.5 font-mono text-xs dark:border-ink-700 dark:bg-ink-950">
        {value}
      </code>
      <button type="button" className="btn shrink-0 px-2" onClick={copy} aria-label={`Copy ${label}`}>
        {copied ? <Check aria-hidden="true" className="h-4 w-4" /> : <Copy aria-hidden="true" className="h-4 w-4" />}
        <span className="sr-only" aria-live="polite">
          {copied ? 'Copied' : ''}
        </span>
      </button>
    </div>
  )
}

function SetupItem({ ok, title, children }: { ok: boolean | null; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3 py-3">
      {ok ? (
        <CheckCircle2 aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-supported-line" />
      ) : (
        <CircleAlert aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-unsupported-line" />
      )}
      <div className="min-w-0 flex-1">
        <p className="font-medium">
          {title}{' '}
          <span className={`text-xs font-semibold uppercase tracking-wide ${ok ? 'text-supported-fg dark:text-supported-bg' : 'text-unsupported-fg dark:text-unsupported-bg'}`}>
            {ok === null ? 'Unknown' : ok ? 'Done' : 'To do'}
          </span>
        </p>
        <div className="mt-1 space-y-2 text-sm text-ink-600 dark:text-ink-300">{children}</div>
      </div>
    </li>
  )
}

const STORAGE_TEXT: Record<Health['storage'], string> = {
  upstash: 'Upstash Redis is connected.',
  memory: 'Running with in-memory storage (local development only; data is lost on restart).',
  missing: 'No storage is connected.',
  error: 'Storage is configured but did not answer. Check the Upstash credentials.',
}

export function Setup({ health, error, loading, onRecheck, signedIn }: Props) {
  const origin = window.location.origin
  const storageOk = health ? health.storage === 'upstash' || health.storage === 'memory' : null
  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Set up the dashboard</h1>
        <button type="button" className="btn" onClick={onRecheck} disabled={loading}>
          <RefreshCw aria-hidden="true" className={`h-4 w-4 ${loading ? 'motion-safe:animate-spin' : ''}`} />
          Check again
        </button>
      </div>
      {error ? <Notice tone="error">Could not read /api/health: {error}</Notice> : null}
      {health?.ok ? (
        <Notice tone="info">
          <CheckCircle2 aria-hidden="true" className="h-4 w-4 shrink-0 text-supported-line" />
          <span>
            The dashboard is configured. {signedIn ? <a href="#/" className="font-medium underline">Go to the overview</a> : <a href="#/" className="font-medium underline">Sign in</a>}.
            {health.retention_days ? ` Runs are kept for ${health.retention_days} days.` : ''}
          </span>
        </Notice>
      ) : null}

      <Panel title="1. Configure this deployment on Vercel">
        {loading && !health ? (
          <Spinner label="Checking settings" />
        ) : (
          <ol className="divide-y divide-ink-100 dark:divide-ink-800">
            <SetupItem ok={storageOk} title="Storage">
              <p>{health ? STORAGE_TEXT[health.storage] : 'Unknown.'}</p>
              {health?.storage !== 'upstash' ? (
                <p>
                  In your Vercel project open the <strong>Storage</strong> tab, choose <strong>Upstash for Redis</strong>, pick the free
                  plan and connect it to this project. Vercel adds the connection variables for you.
                </p>
              ) : null}
            </SetupItem>
            <SetupItem ok={health ? health.ingest_secret : null} title="Ingest secret">
              <p>
                Set the environment variable <code className="code-chip">SAIFE_DASHBOARD_SECRET</code> to a long random string (for example
                the output of <code className="code-chip">openssl rand -hex 32</code>). sAIfe signs every event with it; unsigned events are
                rejected.
              </p>
            </SetupItem>
            <SetupItem ok={health ? health.password : null} title="Dashboard password">
              <p>
                Set <code className="code-chip">DASHBOARD_PASSWORD</code>. Anyone with it can read your runs, so use a strong one.
              </p>
            </SetupItem>
            <li className="py-3 text-sm text-ink-600 dark:text-ink-300">
              After changing environment variables, <strong>redeploy</strong> (Deployments → … → Redeploy) so they take effect.
            </li>
          </ol>
        )}
      </Panel>

      <Panel title="2. Point sAIfe at this dashboard">
        <div className="space-y-3 text-sm text-ink-600 dark:text-ink-300">
          <p>
            Add these lines to the <code className="code-chip">.env</code> file of your sAIfe checkout. Use the same secret as on Vercel.
          </p>
          <CopyField label="dashboard URL line" value={`SAIFE_DASHBOARD_URL=${origin}`} />
          <CopyField label="secret line" value="SAIFE_DASHBOARD_SECRET=<the same secret>" />
          <p>Every run then posts its events here as it goes: claims, judge verdicts, repairs and the final report.</p>
        </div>
      </Panel>

      <Panel title="3. Publish benchmark results (optional)">
        <div className="space-y-3 text-sm text-ink-600 dark:text-ink-300">
          <p>Upload a saved benchmark result:</p>
          <CopyField label="publish command" value="uv run python -m saife publish-bench bench/results/<name>.json" />
          <p>
            When the dashboard is configured, <code className="code-chip">saife bench</code> publishes its result automatically.
          </p>
        </div>
      </Panel>
    </main>
  )
}
