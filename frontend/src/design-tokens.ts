/**
 * BSVibe design tokens — source of truth for runtime CSS vars.
 * Mirrors the canonical ``design_system.md`` v0.1.0 spec (kept in
 * the maintainer's BSVibe-ecosystem doc vault). Run
 * ``pnpm tokens:verify`` to check this stays in sync; pass
 * ``DESIGN_SYSTEM_SPEC=/path/to/spec`` to point the check at a
 * non-default location.
 */

export const gray = {
  50: '#f2f3f7',
  100: '#e4e6ee',
  200: '#c8ccdb',
  300: '#a8adc6',
  400: '#8187a8',
  500: '#5a5f7d',
  600: '#3d4160',
  700: '#2a2d42',
  800: '#1e2033',
  850: '#181926',
  900: '#111218',
  950: '#0a0b0f',
} as const;

export const brand = {
  indigo: '#6366f1',
  blue: '#3b82f6',
  amber: '#f59e0b',
  rose: '#f43f5e',
  emerald: '#10b981',
} as const;

export const product = {
  bsvibe: brand.indigo,
  bsnexus: brand.blue,
  bsgateway: brand.amber,
  bsupervisor: brand.rose,
  bsage: brand.emerald,
} as const;

export const semantic = {
  success: brand.emerald,
  warning: brand.amber,
  error: brand.rose,
  info: brand.blue,
} as const;

export const alias = {
  bgBase: gray[950],
  bgSurface: gray[900],
  bgElevated: gray[850],
  bgHover: gray[800],
  bgActive: gray[700],

  textPrimary: gray[50],
  textSecondary: gray[400],
  textTertiary: gray[500],
  textDisabled: gray[600],
  textInverse: gray[950],

  borderDefault: gray[700],
  borderSubtle: gray[800],
  borderStrong: gray[600],

  accentDefault: product.bsnexus,
} as const;

export const radius = {
  sm: '4px',
  md: '8px',
  lg: '12px',
  xl: '16px',
  full: '9999px',
} as const;

export const spacing = {
  '0.5': '2px',
  '1': '4px',
  '2': '8px',
  '3': '12px',
  '4': '16px',
  '6': '24px',
  '8': '32px',
  '12': '48px',
  '16': '64px',
  '24': '96px',
} as const;

export const typography = {
  fontFamily: {
    sans: '"Plus Jakarta Sans", ui-sans-serif, system-ui, -apple-system, sans-serif',
    mono: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  },
  scale: {
    xs: { size: '0.75rem', lineHeight: '1rem', weight: 400 },
    sm: { size: '0.875rem', lineHeight: '1.25rem', weight: 400 },
    base: { size: '1rem', lineHeight: '1.5rem', weight: 400 },
    lg: { size: '1.125rem', lineHeight: '1.75rem', weight: 500 },
    xl: { size: '1.25rem', lineHeight: '1.75rem', weight: 600 },
    '2xl': { size: '1.5rem', lineHeight: '2rem', weight: 600 },
    '3xl': { size: '1.875rem', lineHeight: '2.25rem', weight: 700 },
    '4xl': { size: '2.25rem', lineHeight: '2.5rem', weight: 700 },
  },
} as const;

export const shadow = {
  sm: '0 1px 2px rgba(0, 0, 0, 0.3)',
  md: '0 4px 12px rgba(0, 0, 0, 0.4)',
  lg: '0 8px 24px rgba(0, 0, 0, 0.5)',
} as const;

export const motion = {
  durationFast: '100ms',
  durationNormal: '200ms',
  durationSlow: '300ms',
  easingDefault: 'cubic-bezier(0.4, 0, 0.2, 1)',
  easingBounce: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
} as const;

export type Gray = typeof gray;
export type Brand = typeof brand;
export type Product = typeof product;
export type Alias = typeof alias;
