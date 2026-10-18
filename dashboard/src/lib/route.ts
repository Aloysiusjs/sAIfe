import { useEffect, useState } from 'react'

export type Route = { page: 'overview' } | { page: 'run'; id: string } | { page: 'bench' } | { page: 'setup' }

const RUN_ID_RE = /^[A-Za-z0-9_-]{1,64}$/

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, '') || '/'
  if (path === '/bench') return { page: 'bench' }
  if (path === '/setup') return { page: 'setup' }
  const m = /^\/run\/(.+)$/.exec(path)
  if (m?.[1]) {
    let id = m[1]
    try {
      id = decodeURIComponent(id)
    } catch {
      /* keep raw */
    }
    if (RUN_ID_RE.test(id)) return { page: 'run', id }
  }
  return { page: 'overview' }
}

export function href(route: Route): string {
  switch (route.page) {
    case 'run':
      return `#/run/${encodeURIComponent(route.id)}`
    case 'bench':
      return '#/bench'
    case 'setup':
      return '#/setup'
    default:
      return '#/'
  }
}

export function navigate(route: Route): void {
  window.location.hash = href(route)
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
  useEffect(() => {
    const on = () => {
      setRoute(parseHash(window.location.hash))
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}
