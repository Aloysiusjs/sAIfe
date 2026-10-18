// WCAG AA check for the status and neutral tokens in tailwind.config.js (N5).
import config from '../tailwind.config.js'

const c = config.theme.extend.colors
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
const lin = (v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
const lum = (h) => {
  const [r, g, b] = hex(h).map(lin)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m)
  return (x + 0.05) / (y + 0.05)
}

const checks = []
for (const s of ['supported', 'likely', 'unsupported', 'contradicted', 'unverifiable']) {
  checks.push([`${s}.fg on ${s}.bg`, c[s].fg, c[s].bg, 4.5])
  checks.push([`${s}.fg on white`, c[s].fg, '#ffffff', 4.5])
  checks.push([`ink.900 on ${s}.bg`, c.ink[900], c[s].bg, 4.5])
  checks.push([`${s}.line on white`, c[s].line, '#ffffff', 3])
}
checks.push(['ink.500 on white', c.ink[500], '#ffffff', 4.5])
checks.push(['ink.500 on canvas', c.ink[500], c.canvas, 4.5])
checks.push(['ink.600 on ink.50', c.ink[600], c.ink[50], 4.5])
checks.push(['white on action', '#ffffff', c.action.DEFAULT, 4.5])
checks.push(['action.fg on action.soft', c.action.fg, c.action.soft, 4.5])
checks.push(['ink.400 border on white (non-text)', c.ink[400], '#ffffff', 3])

let failed = 0
for (const [name, fg, bg, min] of checks) {
  const r = ratio(fg, bg)
  const ok = r >= min
  if (!ok) failed++
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${r.toFixed(2).padStart(5)}:1  (min ${min})  ${name}`)
}
process.exit(failed ? 1 : 0)
