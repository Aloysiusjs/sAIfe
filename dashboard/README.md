# sAIfe Monitor

A dashboard for watching your sAIfe judges at work: which claims they flagged as
hallucinations, which passed, what was repaired, and how benchmark runs scored. It runs on
**Vercel's free (Hobby) tier** with a **free Upstash Redis** database. Each user deploys
their own copy, so nobody sees anyone else's runs.

```
your machine                                   your Vercel project (free)
saife check / run / bench ──HMAC-signed──▶ /api/ingest, /api/bench ──▶ Upstash Redis
                                                      ▲
browser ──password──▶ dashboard pages ──▶ /api/runs, /api/run, /api/bench
```

## Deploy (about 10 minutes)

1. **Push the repo to GitHub** (your own fork or copy).
2. **Import it on Vercel**: <https://vercel.com/new> → pick the repo → set **Root
   Directory** to `dashboard`. The framework (Vite), build command and output come from
   `vercel.json`. Deploy.
3. **Add free storage**: in the project, open **Storage** → **Create** → **Upstash for
   Redis** → free plan → connect it to the project. This sets `KV_REST_API_URL` and
   `KV_REST_API_TOKEN` for you.
4. **Set two environment variables** (Settings → Environment Variables), then redeploy:

   | Name | Value |
   | --- | --- |
   | `SAIFE_DASHBOARD_SECRET` | A long random string, e.g. the output of `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
   | `DASHBOARD_PASSWORD` | The password you will type to open the dashboard. Make it long: there is no lockout |

5. **Point sAIfe at it**: in sAIfe's `.env` (repo root):

   ```
   SAIFE_DASHBOARD_URL=https://<your-project>.vercel.app
   SAIFE_DASHBOARD_SECRET=<the same secret as on Vercel>
   ```

   `uv run python -m saife config` should now show `dashboard <your-project>.vercel.app`.
6. Run any check, e.g. `uv run python -m saife check -a examples/answer_with_errors.md -s examples/sources`,
   open the dashboard, enter your password and watch it arrive.

Benchmarks: `saife bench` publishes its summary automatically when the dashboard is
configured; publish an older result with
`uv run python -m saife publish-bench bench/results/<name>.json`. Only `meta` and
`metrics` are sent, never the per-item details.

## What it shows

- **Overview**: runs, hallucinations caught (contradicted + unsupported claims), claims
  passed (supported), unverified claims, answer outcomes (verified / caution / blocked),
  runs in progress, claims per day by status, which judge models ruled, and a filterable
  run list. It refreshes every 5 seconds while the tab is open.
- **Run detail**: every claim with its status and each judge's verdict, model, confidence
  and rationale; evidence quotes with a "verified by code" mark; repairs (before → after,
  reason); alerts; the final answer and the original draft. Runs in progress update live.
- **Benchmarks**: precision, recall, F1, false-alarm rate and residual hallucination
  before → after for seeded runs; flag rate for trap runs; silent failures, latency, cost.
- **Setup**: which settings the deployment has (never their values) and what to do next.

## Security and privacy

- **Writes** need an HMAC-SHA256 signature made with `SAIFE_DASHBOARD_SECRET` over the exact
  request body: the same scheme as sAIfe's webhook (`X-Saife-Signature: sha256=<hex>`).
- **Reads** need `Authorization: Bearer <DASHBOARD_PASSWORD>`. The browser keeps the
  password in local storage until you sign out.
- If the secret, the password or the storage is missing, the API answers 503: the dashboard
  never runs open.
- Runs contain claims and source excerpts. They are kept for **30 days** (newest 500
  runs, 100 benchmark results) and then expire. sAIfe redacts API keys before sending.
- Pages are served with a strict Content-Security-Policy, `noindex`, and no third-party
  scripts, fonts or analytics.
- Known limits: no per-user accounts (one password per deployment), no rate limiting on
  password attempts, and a captured signed request could be replayed (it would only
  re-store the same event).

## Free-tier fit

- Vercel Hobby (non-commercial use): static pages plus five small functions.
- Upstash free: about 500,000 commands a month. A run sends one request per pipeline
  event (typically 10–40) and each costs about 5 Redis commands, so roughly 2,500–10,000
  runs a month. The dashboard's 5-second polling adds about 3 commands per refresh while a
  tab is open.

## Develop locally

```bash
cd dashboard
npm install
npm run dev        # http://localhost:5174, API + in-memory store, password "dev"
npm test           # API and signature tests (node --test, no extra dependencies)
npm run build      # type-check and production build
```

To send real runs to the dev server, set `SAIFE_DASHBOARD_URL=http://localhost:5174` and
`SAIFE_DASHBOARD_SECRET=dev-secret-for-local-use` (the dev server's default) in sAIfe's `.env`.
