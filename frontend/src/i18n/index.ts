// Phase C — BSNexus i18n root.
//
// We deliberately keep locale state on the client (localStorage) and
// avoid a ``[locale]`` segment in the App Router. The (authed) layout
// group, all existing routes (``/dashboard``, ``/projects/:id``,
// ``/settings``), and every e2e spec depend on the current path shape;
// adding a locale segment would be a churn-only refactor for Phase C.
//
// next-intl v3's ``NextIntlClientProvider`` accepts ``messages`` +
// ``locale`` props directly, which fits this client-side model. The
// ``IntlProvider`` wrapper lives in ``app/providers.tsx``.
//
// This file replaces the legacy ``src/i18n/ko/translation.json`` +
// ``src/i18n/en/translation.json`` setup that was wired through
// ``i18next`` / ``react-i18next``. Those packages are removed in this
// PR (see package.json delta) — they were never imported beyond
// ``providers.tsx``'s side-effect ``import '../i18n'``, so the cutover
// is contained.

import enMessages from '../../messages/en.json'
import koMessages from '../../messages/ko.json'

export type Locale = 'ko' | 'en'

export const SUPPORTED_LOCALES: readonly Locale[] = ['ko', 'en'] as const
export const DEFAULT_LOCALE: Locale = 'ko'
export const LOCALE_STORAGE_KEY = 'bsnexus.locale'

export const messagesByLocale: Record<Locale, typeof enMessages> = {
  en: enMessages,
  ko: koMessages,
}

export function isLocale(value: string | null | undefined): value is Locale {
  return value === 'ko' || value === 'en'
}

export function readStoredLocale(): Locale {
  if (typeof window === 'undefined') return DEFAULT_LOCALE
  const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY)
  return isLocale(stored) ? stored : DEFAULT_LOCALE
}

export function writeStoredLocale(locale: Locale): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(LOCALE_STORAGE_KEY, locale)
}
