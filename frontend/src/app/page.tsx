import { Suspense } from 'react'
import LandingPage from '../components/pages/LandingPage'

/**
 * Public landing route. ``LandingPage`` is a client component (uses
 * ``useAuthContext`` + ``useRouter``); the wrapping page is a server
 * component that ships zero JS by default.
 *
 * Wrapping in ``<Suspense>`` is defensive — Next.js 15 requires it for
 * any client subtree that calls ``useSearchParams()``. LandingPage
 * doesn't today, but the guard keeps refactors safe.
 */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <LandingPage />
    </Suspense>
  )
}
