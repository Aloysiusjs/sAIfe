// Lets `node --test` run the TypeScript sources directly (Node >= 22.18 strips types).
// The api/ files import siblings as "./x.js" (what Vercel's Node runtime needs); this hook
// resolves such a specifier to the neighbouring .ts file when the .js one does not exist.
import { register } from 'node:module'

register(
  'data:text/javascript,' +
    encodeURIComponent(`
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
export async function resolve(specifier, context, next) {
  if (specifier.startsWith('.') && specifier.endsWith('.js') && context.parentURL) {
    const ts = new URL(specifier.slice(0, -3) + '.ts', context.parentURL)
    if (!existsSync(fileURLToPath(new URL(specifier, context.parentURL))) && existsSync(fileURLToPath(ts))) {
      return next(ts.href, context)
    }
  }
  return next(specifier, context)
}`),
)
