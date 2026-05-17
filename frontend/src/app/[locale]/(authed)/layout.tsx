import Layout from '../../../components/layout/Layout'
import ProtectedRoute from '../../../components/auth/ProtectedRoute'

/**
 * Route-group layout that gates every authed page (``/dashboard``,
 * ``/projects/...``, ``/settings``) on a verified session and wraps
 * children in the BSNexus chrome (Sidebar / GlobalChat / palette).
 *
 * ``(authed)`` is a Next.js route group — it does not appear in the
 * URL, it just lets us share this layout without adding a path
 * segment.
 */
export default function AuthedLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <ProtectedRoute>
      <Layout>{children}</Layout>
    </ProtectedRoute>
  )
}
