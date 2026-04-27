'use client'

import { useCallback, useMemo, useState } from 'react'
import { NextIntlClientProvider } from 'next-intl'

import {
  DEFAULT_LOCALE,
  type Locale,
  messagesByLocale,
  readStoredLocale,
  writeStoredLocale,
} from './index'
import {
  LocaleContext,
  type LocaleContextValue,
} from './LocaleContext'

/**
 * Mounts ``NextIntlClientProvider`` with messages loaded by
 * locale and exposes a ``LocaleContext`` so the Settings language
 * switcher can call ``setLocale`` without touching the provider tree.
 *
 * SSR keeps ``DEFAULT_LOCALE`` (ko); the first client effect re-hydrates
 * from ``localStorage`` if the founder previously picked English. We
 * avoid ``useEffect`` for the initial read by using a lazy ``useState``
 * initializer — that pattern is React-Strict-Mode safe and survives
 * the double-invoke without flicker.
 */
export default function IntlProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    // Lazy init runs once per browser session. ``readStoredLocale``
    // guards on ``typeof window`` so it's safe on the server pass.
    return readStoredLocale()
  })

  const setLocale = useCallback((next: Locale) => {
    writeStoredLocale(next)
    setLocaleState(next)
  }, [])

  const ctx = useMemo<LocaleContextValue>(
    () => ({ locale, setLocale }),
    [locale, setLocale],
  )

  return (
    <LocaleContext.Provider value={ctx}>
      <NextIntlClientProvider
        locale={locale}
        messages={messagesByLocale[locale] ?? messagesByLocale[DEFAULT_LOCALE]}
        timeZone="Asia/Seoul"
      >
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  )
}
