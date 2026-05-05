'use client'

import { DemoBanner, useAutoDemoSession } from '@bsvibe/demo'

const DEMO_API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? 'https://api-demo-nexus.bsvibe.dev'

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
  const { loading, error } = useAutoDemoSession(DEMO_API_URL)

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
    <>
      <DemoBanner productName="BSNexus" locale="en" />
      {children}
    </>
  )
}
