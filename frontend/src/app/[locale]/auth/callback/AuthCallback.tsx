'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { consumeAuthCallback } from '../../../../hooks/useAuth'

/**
 * Client-only auth callback. Parses access_token / refresh_token from
 * either the URL fragment (``#access_token=…``) or the query string
 * (``?access_token=…``) via ``consumeAuthCallback``, then bounces to
 * ``/dashboard``. Independent of the legacy ``#/auth/callback`` hash
 * route the SPA handled inline — both paths now work.
 */
export default function AuthCallback() {
  const router = useRouter()

  useEffect(() => {
    consumeAuthCallback()
    router.replace('/dashboard')
  }, [router])

  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-accent" />
    </div>
  )
}
