/** @type {import('tailwindcss').Config} */

/*
 * sAIfe is an instrument, not a brochure: cool ink neutrals, white work surfaces,
 * one restrained blue for actions, and five status hues that only ever appear with
 * their icon and label (N5). Each status has three tokens with separate jobs:
 *   fg   label and icon text (>= 4.5:1 on both `bg` and white)
 *   bg   the tint behind a highlighted span or badge
 *   line the underline or border that marks the span (>= 3:1 on white)
 * scripts/check-contrast.mjs verifies every pairing.
 */

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'media',
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#0e131b',
          900: '#161c26',
          800: '#232b38',
          700: '#323b4a',
          600: '#4a5363',
          500: '#5a6474',
          400: '#8a93a2',
          300: '#c3c9d2',
          200: '#dde1e7',
          100: '#ebeef2',
          50: '#f4f6f8',
        },
        canvas: '#f1f3f6',
        action: { DEFAULT: '#1f4ea3', hover: '#183f86', soft: '#e7eefa', fg: '#1a4590' },
        supported: { fg: '#146138', bg: '#e3f3e9', line: '#2b8a55' },
        likely: { fg: '#7a4f00', bg: '#fbf0d4', line: '#b07a10' },
        unsupported: { fg: '#9a3410', bg: '#fde9df', line: '#c4521f' },
        contradicted: { fg: '#a3162b', bg: '#fbe5e8', line: '#c8283f' },
        unverifiable: { fg: '#3d4757', bg: '#e8ebf0', line: '#6b7586' },
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif'],
        serif: ['"Iowan Old Style"', '"Palatino Linotype"', 'Palatino', 'Georgia', 'serif'],
        mono: ['ui-monospace', '"Cascadia Mono"', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.02em' }],
      },
      boxShadow: {
        panel: '0 1px 2px rgba(14, 19, 27, 0.05), 0 2px 8px -2px rgba(14, 19, 27, 0.06)',
        drawer: '0 12px 40px -8px rgba(14, 19, 27, 0.22), 0 4px 12px -4px rgba(14, 19, 27, 0.10)',
      },
      transitionTimingFunction: {
        out: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [],
}
