import { defaultDeps, ingest } from './_lib/service.js'

/** sAIfe's WebhookSink posts every pipeline event here (HMAC-signed). */
export async function POST(request: Request): Promise<Response> {
  return ingest(request, defaultDeps())
}
