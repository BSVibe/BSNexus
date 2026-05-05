'use client'

import { DemoBanner, useAutoDemoSession } from '@bsvibe/demo'
import { AuthContext } from '../auth/AuthContext'
import { injectDemoToken } from '../../hooks/useAuth'

const DEMO_API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? 'https://api-demo-nexus.bsvibe.dev'

const DEMO_USER = {
  id: 'demo-user',
  email: 'demo@bsvibe.dev',
  tenantId: 'demo',
  tenantName: 'Demo sandbox',
  role: 'demo',
}

const DEMO_AUTH_VALUE = {
  user: DEMO_USER,
  loading: false,
  login: () => {
    /* demo: no-op */
  },
  logout: async () => {
    /* demo: no-op */
  },
  tenants: [{ id: 'demo', name: 'Demo sandbox', role: 'demo' }],
  switchTenant: async () => {
    /* demo: single tenant */
  },
}

/**
 * Drop-in replacement for ``AuthProvider`` in demo mode. Auto-creates a
 * sandbox session on mount, shows a top banner, and renders children
 * without going through prod's JWT probe.
 *
 * Build-time switch in ``providers.tsx`` chooses this vs ``AuthProvider``.
 */
export default function DemoModeProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const { loading, error } = useAutoDemoSession(DEMO_API_URL, {
    onSessionReady: ({ token, expiresIn }) => {
      // Park the demo JWT in cachedToken so api/client's
      // getAccessToken() returns it. Without this, the
      // AuthContext stub above lets the page render but the
      // axios client still goes out without Authorization and
      // every dashboard fetch 401s.
      injectDemoToken(token, expiresIn)
    },
  })

  if (loading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0d0e14',
          color: '#a8adc6',
        }}
      >
        <div
          style={{
            width: 32,
            height: 32,
            border: '3px solid #2a2d42',
            borderTopColor: '#3b82f6',
            borderRadius: '50%',
            animation: 'spin 0.9s linear infinite',
          }}
        />
        <p style={{ fontSize: 14 }}>Setting up your demo sandbox…</p>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    )
  }

  if (error) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0d0e14',
          color: '#f9fafb',
        }}
      >
        <div style={{ textAlign: 'center', padding: 32 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 8 }}>
            Demo unavailable
          </h1>
          <p style={{ color: '#a8adc6', fontSize: 14 }}>{error}</p>
        </div>
      </div>
    )
  }

  return (
    <AuthContext.Provider value={DEMO_AUTH_VALUE}>
      <DemoBanner productName="BSNexus" locale="en" />
      {children}
    </AuthContext.Provider>
  )
}
