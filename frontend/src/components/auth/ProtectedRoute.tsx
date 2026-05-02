'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthContext } from './AuthContext'

/**
 * App Router replacement for the legacy react-router-dom ProtectedRoute.
 * Wraps authenticated layouts/pages — when the auth probe finishes and
 * there is no user we redirect to ``/``. ``children`` is the protected
 * tree (layouts pass it as ``{children}``).
 */
export default function ProtectedRoute({
  children,
}: {
  children: React.ReactNode
}) {
  const { user, loading } = useAuthContext()
  const router = useRouter()

  useEffect(() => {
    if (!loading && !user) {
      router.replace('/')
    }
  }, [loading, user, router])

  if (loading || !user) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-accent" />
      </div>
    )
  }

  return <>{children}</>
}
