import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'
import nextPlugin from '@next/eslint-plugin-next'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', '.next', 'node_modules', 'next-env.d.ts']),
  // Register the Next.js plugin + rules globally (no `files` filter) so
  // `next build`'s detection probe sees `@next/next` in the resolved
  // config for `eslint.config.js` itself and doesn't print
  // "The Next.js plugin was not detected in your ESLint configuration."
  // The rules themselves only fire on app source patterns, so leaving
  // them at the top level is harmless for `.js` config files.
  {
    plugins: { '@next/next': nextPlugin },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs['core-web-vitals'].rules,
    },
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
  // E2E / exploratory spec files — playwright scratchpads carry looser
  // types (API response `any`, disposable locals) on purpose. Keep the
  // rule strict for app code; relax it for the specs.
  {
    files: ['e2e/specs/**/*.ts'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': 'off',
    },
  },
])
