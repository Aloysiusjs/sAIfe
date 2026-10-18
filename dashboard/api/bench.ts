import { defaultDeps, ingestBench, listBench } from './_lib/service.js'

/** GET: benchmark summaries (password). POST: `saife publish-bench` uploads one (HMAC-signed). */
export async function GET(request: Request): Promise<Response> {
  return listBench(request, defaultDeps())
}

export async function POST(request: Request): Promise<Response> {
  return ingestBench(request, defaultDeps())
}
