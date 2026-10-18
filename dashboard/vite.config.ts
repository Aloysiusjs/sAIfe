import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import type { IncomingMessage } from 'node:http'
import { defineConfig, type Plugin, type ViteDevServer } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Local development only: serve api/*.ts through Vite so `npm run dev` runs the same
 * handlers Vercel runs in production, backed by an in-memory store. Missing settings
 * get development defaults, printed at startup; production never uses these.
 */
function devApi(): Plugin {
  return {
    name: 'saife-dev-api',
    apply: 'serve',
    configureServer(server: ViteDevServer) {
      process.env.SAIFE_DASHBOARD_DEV = '1'
      const defaults: Record<string, string> = {
        SAIFE_DASHBOARD_SECRET: 'dev-secret-for-local-use',
        DASHBOARD_PASSWORD: 'dev',
      }
      for (const [k, v] of Object.entries(defaults)) {
        if (!process.env[k]) {
          process.env[k] = v
          server.config.logger.info(`  [dev api] ${k} not set; using the development value "${v}"`)
        }
      }
      server.middlewares.use(async (req, res, next) => {
        const url = new URL(req.url ?? '/', 'http://localhost')
        const m = /^\/api\/([a-z]+)$/.exec(url.pathname)
        const file = m ? resolve(__dirname, 'api', `${m[1]}.ts`) : null
        if (!file || !existsSync(file)) return next()
        try {
          const mod = (await server.ssrLoadModule(file)) as Record<string, unknown>
          const handler = mod[req.method ?? 'GET']
          if (typeof handler !== 'function') {
            res.statusCode = 405
            res.end('method not allowed')
            return
          }
          const body = req.method === 'GET' || req.method === 'HEAD' ? undefined : await readAll(req)
          const headers = new Headers()
          for (const [k, v] of Object.entries(req.headers)) if (typeof v === 'string') headers.set(k, v)
          const request = new Request(url, { method: req.method, headers, body })
          const response = (await handler(request)) as Response
          res.statusCode = response.status
          response.headers.forEach((v, k) => res.setHeader(k, v))
          res.end(Buffer.from(await response.arrayBuffer()))
        } catch (e) {
          server.config.logger.error(`[dev api] ${String(e)}`)
          res.statusCode = 500
          res.end('dev api error')
        }
      })
    },
  }
}

function readAll(req: IncomingMessage): Promise<Buffer> {
  return new Promise((ok, fail) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => ok(Buffer.concat(chunks)))
    req.on('error', fail)
  })
}

export default defineConfig({
  plugins: [react(), devApi()],
  server: { port: 5174 },
})
