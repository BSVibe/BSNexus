/**
 * BSNexus i18n middleware.
 *
 * Uses the BSVibe shared `@bsvibe/i18n/middleware` factory so locale
 * routing — and the production re-entrancy guard — stay consistent across
 * every consumer product. BSNexus keeps the package default
 * `defaultLocale: 'ko'` — the UI copy and Playwright e2e suite assert on
 * Korean. English is opt-in via the `/en` URL prefix.
 *
 * `localeDetection: false` is required here (BSNexus is the one product
 * with a non-`en` default): with `localePrefix: 'as-needed'`, leaving
 * detection on lets `Accept-Language` redirect every `en-US` browser off
 * the bare Korean path to `/en`, breaking the flat-URL-renders-Korean
 * contract. The shared factory exposes this knob as of `@bsvibe/i18n`
 * 0.3.0, so BSNexus no longer composes `next-intl/middleware` directly.
 *
 * This middleware is i18n-only. BSNexus auth is enforced client-side by
 * `ProtectedRoute`; a middleware redirect would risk breaking the public
 * landing page (`/`) and the OAuth callback (`/auth/callback`).
 */
import { createI18nMiddleware } from '@bsvibe/i18n/middleware'

export default createI18nMiddleware({
  locales: ['ko', 'en'],
  defaultLocale: 'ko',
  localePrefix: 'as-needed',
  localeDetection: false,
})

// NOTE: Next.js parses `config.matcher` statically — spread operators or
// computed values are rejected (`Invalid page config`). The literal mirrors
// `defaultMatcher` from `@bsvibe/i18n/middleware`; keep them in sync.
export const config = {
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)'],
}
