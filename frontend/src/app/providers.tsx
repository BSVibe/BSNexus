'use client'

import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import AuthProvider from '../components/auth/AuthProvider'
import { ToastContainer } from '../components/common'
import '../i18n'

/**
 * Client-side providers shared by every page. ``QueryClient`` is created
 * once per browser session via ``useState`` so React Strict Mode's
 * double-invoke doesn't churn it. ``AuthProvider`` blocks render while
 * the JWT probe runs; ``ToastContainer`` is mounted at the root so
 * toasts overlay any route.
 */
export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient())

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        {children}
        <ToastContainer />
      </AuthProvider>
    </QueryClientProvider>
  )
}
