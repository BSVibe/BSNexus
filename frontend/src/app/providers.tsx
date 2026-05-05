'use client'

import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { isDemoMode } from '@bsvibe/demo'

import AuthProvider from '../components/auth/AuthProvider'
import DemoModeProvider from '../components/demo/DemoModeProvider'
import { ToastContainer } from '../components/common'
import IntlProvider from '../i18n/IntlProvider'

/**
 * Client-side providers shared by every page. ``QueryClient`` is created
 * once per browser session via ``useState`` so React Strict Mode's
 * double-invoke doesn't churn it. ``AuthProvider`` blocks render while
 * the JWT probe runs; ``ToastContainer`` is mounted at the root so
 * toasts overlay any route.
 *
 * ``IntlProvider`` wraps next-intl's ``NextIntlClientProvider`` and the
 * ``LocaleContext`` switcher. It sits inside the auth boundary so the
 * Settings language picker can persist without flashing the loading
 * spinner first; messages are bundled at build time so there's no
 * async hop on locale change.
 */
export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient())
  // Build-time switch — demo deployments never instantiate AuthProvider, so
  // the JWT probe + login redirect logic is fully tree-shaken from the bundle.
  const Gate = isDemoMode() ? DemoModeProvider : AuthProvider

  return (
    <QueryClientProvider client={queryClient}>
      <IntlProvider>
        <Gate>
          {children}
          <ToastContainer />
        </Gate>
      </IntlProvider>
    </QueryClientProvider>
  )
}
