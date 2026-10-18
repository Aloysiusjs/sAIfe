import type { KeyboardEvent } from 'react'

/**
 * Arrow-key movement between claim controls inside a container. Tab still works
 * because every claim stays in the tab order; arrows are a faster path for long answers.
 */
export function onClaimArrowKeys(e: KeyboardEvent<HTMLElement>) {
  const keys = ['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End']
  if (!keys.includes(e.key)) return
  const target = e.target as HTMLElement
  if (!target.matches('[data-claim-nav]')) return
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[data-claim-nav]'))
  const at = items.indexOf(target)
  if (at < 0) return
  e.preventDefault()
  let next = at
  if (e.key === 'Home') next = 0
  else if (e.key === 'End') next = items.length - 1
  else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = Math.min(items.length - 1, at + 1)
  else next = Math.max(0, at - 1)
  items[next]?.focus()
}

/** Enter/Space activation for non-button elements that act as buttons. */
export function activateOnKey(e: KeyboardEvent<HTMLElement>, fn: () => void) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    fn()
  }
}
