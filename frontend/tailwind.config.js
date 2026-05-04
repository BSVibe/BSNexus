// Tailwind config is ESM (``export default``); the typography plugin
// must be imported, not require()'d — Next 15's stricter ESM loader
// surfaces ``ReferenceError: require is not defined`` when the dev
// cache miscompiles the previous CJS form.
import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
    // Scan @bsvibe/layout (ResponsiveSidebar / SidebarBrand / SidebarUserCard
    // ship Tailwind utility classes inline) so the generated CSS contains
    // their `border-l-4`, `min-h-[44px]`, `bg-gray-950`, etc. utilities.
    './node_modules/@bsvibe/layout/dist/**/*.{js,jsx,ts,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      colors: {
        gray: {
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
        },
        brand: {
          indigo: '#6366f1',
          blue: '#3b82f6',
          amber: '#f59e0b',
          rose: '#f43f5e',
          emerald: '#10b981',
        },
        product: {
          bsvibe: '#6366f1',
          bsnexus: '#3b82f6',
          bsgateway: '#f59e0b',
          bsupervisor: '#f43f5e',
          bsage: '#10b981',
        },
        bg: {
          base: 'var(--bg-base)',
          surface: 'var(--bg-surface)',
          elevated: 'var(--bg-elevated)',
          hover: 'var(--bg-hover)',
        },
        text: {
          primary: 'var(--text-primary)',
          secondary: 'var(--text-secondary)',
          tertiary: 'var(--text-tertiary)',
          disabled: 'var(--text-disabled)',
        },
        border: {
          DEFAULT: 'var(--border-default)',
          subtle: 'var(--border-subtle)',
          strong: 'var(--border-strong)',
        },
        accent: {
          DEFAULT: 'var(--accent)',
          glow: 'var(--accent-glow)',
        },
      },
      borderRadius: {
        sm: 'var(--r-sm)',
        md: 'var(--r-md)',
        lg: 'var(--r-lg)',
        xl: 'var(--r-xl)',
        full: 'var(--r-full)',
      },
      boxShadow: {
        sm: 'var(--sh-sm)',
        md: 'var(--sh-md)',
        lg: 'var(--sh-lg)',
      },
      transitionTimingFunction: {
        ease: 'cubic-bezier(0.4, 0, 0.2, 1)',
      },
    },
  },
  plugins: [typography],
}
