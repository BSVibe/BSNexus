'use client'

import { createContext, useContext } from 'react'

import { DEFAULT_LOCALE, type Locale } from './index'

export interface LocaleContextValue {
  locale: Locale
  setLocale: (next: Locale) => void
}

export const LocaleContext = createContext<LocaleContextValue>({
  locale: DEFAULT_LOCALE,
  setLocale: () => {
    // no-op default — overridden by ``IntlProvider``.
  },
})

/**
 * Read the current UI locale + a setter that persists to localStorage.
 *
 * Keep this hook stable: components that don't need to re-render on
 * locale change should call ``useTranslations`` directly instead.
 */
export function useLocale(): LocaleContextValue {
  return useContext(LocaleContext)
}
