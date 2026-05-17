'use client'

import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { isDemoMode } from '@bsvibe/demo'

import AuthProvider from '../../components/auth/AuthProvider'
import DemoModeProvider from '../../components/demo/DemoModeProvider'
import { ToastContainer } from '../../components/common'

/**
 * Client-side providers shared by every page. ``QueryClient`` is created
 * once per browser session via ``useState`` so React Strict Mode's
 * double-invoke doesn't churn it. ``AuthProvider`` blocks render while
 * the JWT probe runs; ``ToastContainer`` is mounted at the root so
 * toasts overlay any route.
 *
 * next-intl context is no longer mounted here — the ``[locale]`` root
 * layout wraps the whole tree in ``BSVibeIntlProvider`` from
 * ``@bsvibe/i18n`` *outside* this component, so the provider exists
 * before anything inside ``Providers`` renders. Locale is now derived
 * from the URL segment (``localePrefix: 'as-needed'``, default ``ko``)
 * instead of localStorage.
 */
export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient())
  // Build-time switch — demo deployments never instantiate AuthProvider, so
  // the JWT probe + login redirect logic is fully tree-shaken from the bundle.
  const Gate = isDemoMode() ? DemoModeProvider : AuthProvider

  return (
    <QueryClientProvider client={queryClient}>
      <Gate>
        {children}
        <ToastContainer />
      </Gate>
    </QueryClientProvider>
  )
}
