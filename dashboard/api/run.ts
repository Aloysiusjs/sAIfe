import { defaultDeps, getRun } from './_lib/service.js'

/** One run: summary, full report (once completed) and its events (password). */
export async function GET(request: Request): Promise<Response> {
  return getRun(request, defaultDeps())
}
