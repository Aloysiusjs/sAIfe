import { defaultDeps, listRuns } from './_lib/service.js'

/** Newest runs first, as summaries (password). */
export async function GET(request: Request): Promise<Response> {
  return listRuns(request, defaultDeps())
}
