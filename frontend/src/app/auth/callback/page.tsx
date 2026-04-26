import { Suspense } from 'react'
import AuthCallback from './AuthCallback'

/**
 * BSVibe-Auth redirects users back here after sign-in. ``useAuth`` (the
 * shared hook) already understands ``#/auth/callback`` style hash
 * tokens; this dedicated route handles the cleaner ``/auth/callback``
 * form for callers that prefer path-based redirects. Either path
 * lands at ``/dashboard`` once tokens are persisted.
 */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <AuthCallback />
    </Suspense>
  )
}
