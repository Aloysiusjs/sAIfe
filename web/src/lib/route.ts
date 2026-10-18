import { useEffect, useState } from 'react'

export type Route = 'check' | 'results'

const routeOf = (path: string): Route => (path.replace(/\/+$/, '') === '/results' ? 'results' : 'check')

/** Two routes do not justify a router dependency: pushState plus popstate. */
export function useRoute(): [Route, (to: Route) => void] {
  const [route, setRoute] = useState<Route>(() => routeOf(window.location.pathname))
  useEffect(() => {
    const onPop = () => setRoute(routeOf(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const go = (to: Route) => {
    const path = to === 'results' ? '/results' : '/'
    if (window.location.pathname !== path) window.history.pushState(null, '', path + window.location.search)
    setRoute(to)
    window.scrollTo(0, 0)
  }
  return [route, go]
}

export function demoRequested(): boolean {
  const v = new URLSearchParams(window.location.search).get('demo')
  return v !== null && v !== '0' && v !== 'false'
}
