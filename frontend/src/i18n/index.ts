// BSNexus i18n — locale type exports.
//
// The bespoke client-side next-intl setup (localStorage-backed
// `IntlProvider` + `LocaleContext`) was retired in favour of the shared
// `@bsvibe/i18n` package: a `[locale]` route segment, i18n middleware,
// `src/i18n/request.ts`, and `BSVibeIntlProvider` in the root layout.
//
// Messages now load via `request.ts` (server-side) — no `messagesByLocale`
// table is bundled into the client. The current locale comes from the URL
// segment, read with `useCurrentLocale()` from `@bsvibe/i18n`.
//
// This file only retains the locale type/const exports that product code
// (e.g. the Sidebar locale switcher) still references.

export type Locale = 'ko' | 'en'

export const SUPPORTED_LOCALES: readonly Locale[] = ['ko', 'en'] as const
export const DEFAULT_LOCALE: Locale = 'ko'

export function isLocale(value: string | null | undefined): value is Locale {
  return value === 'ko' || value === 'en'
}
