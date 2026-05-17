/**
 * BSNexus i18n middleware.
 *
 * Routes locales using the BSVibe shared locale config (`createI18nConfig`
 * from `@bsvibe/i18n`) so the locale set + URL-prefix policy stay
 * consistent across consumer products. BSNexus keeps the package default
 * `defaultLocale: 'ko'` — the UI copy and Playwright e2e suite assert on
 * Korean. English is opt-in via the `/en` URL prefix.
 *
 * Why this composes `next-intl/middleware` directly instead of calling
 * `@bsvibe/i18n/middleware`'s `createI18nMiddleware`: that factory does
 * not expose `localeDetection`, which defaults to `true` in next-intl.
 * With `localePrefix: 'as-needed'` and a non-`en` default, detection
 * silently redirects every `en-US` browser from the bare path to `/en`
 * — which breaks the contract that flat URLs render Korean (and would
 * force a mass e2e rewrite). BSupervisor never hit this because its
 * default IS `en`, so detection is a no-op there. We still go through
 * the shared `createI18nConfig` for the locale set / prefix policy and
 * only add the one knob the shared factory omits: `localeDetection`.
 *
 * This middleware is i18n-only. BSNexus auth is enforced client-side by
 * `ProtectedRoute`; a middleware redirect would risk breaking the public
 * landing page (`/`) and the OAuth callback (`/auth/callback`).
 */
import createIntlMiddleware from 'next-intl/middleware'
import { createI18nConfig } from '@bsvibe/i18n'

const i18nConfig = createI18nConfig({
  locales: ['ko', 'en'],
  defaultLocale: 'ko',
  localePrefix: 'as-needed',
})

export default createIntlMiddleware({
  locales: [...i18nConfig.locales],
  defaultLocale: i18nConfig.defaultLocale,
  localePrefix: i18nConfig.localePrefix,
  // Bare paths always mean the default locale (`ko`). Without this,
  // Accept-Language detection redirects English browsers to `/en`.
  localeDetection: false,
})

// NOTE: Next.js parses `config.matcher` statically — spread operators or
// computed values are rejected (`Invalid page config`). The literal mirrors
// `defaultMatcher` from `@bsvibe/i18n/middleware`; keep them in sync.
export const config = {
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)'],
}
