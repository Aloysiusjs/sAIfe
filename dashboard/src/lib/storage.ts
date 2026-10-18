/** Password persistence. localStorage can throw (private mode, blocked storage), so fall back to memory. */

const KEY = 'saife-dashboard-password'
let memory: string | null = null

export function loadPassword(): string | null {
  try {
    const v = window.localStorage.getItem(KEY)
    if (v) return v
  } catch {
    /* storage unavailable */
  }
  return memory
}

export function savePassword(password: string): void {
  memory = password
  try {
    window.localStorage.setItem(KEY, password)
  } catch {
    /* in-memory only */
  }
}

export function clearPassword(): void {
  memory = null
  try {
    window.localStorage.removeItem(KEY)
  } catch {
    /* nothing to clear */
  }
}
