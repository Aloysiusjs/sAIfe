import { defaultDeps, health } from './_lib/service.js'

/** Which settings are present, never their values. Used by the setup screen. */
export async function GET(): Promise<Response> {
  return health(defaultDeps())
}
