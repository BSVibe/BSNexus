import { useEffect, useState } from 'react'

interface User {
  id: string
  email: string
  tenantId: string
  role: string
}

const AUTH_URL = 'https://auth.bsvibe.dev'
const STORED_TOKEN_KEY = 'bsnexus_access_token'
const STORED_REFRESH_KEY = 'bsnexus_refresh_token'

interface SessionResponse {
  access_token: string
  refresh_token: string
  expires_in: number
}

let cachedToken: { value: string; expiresAt: number } | null = null

function isExpired(token: string): boolean {
  try {
    const payload = decodeJwt(token) as { exp?: number }
    if (!payload.exp) return false
    return Date.now() / 1000 >= payload.exp - 30
  } catch {
    return true
  }
}

/**
 * Read tokens from URL hash fragment (#access_token=...&refresh_token=...) and
 * persist them. Used after redirect from auth.bsvibe.dev/login when running on
 * a cross-origin host (e.g. bsserver:3001) where session cookies are not
 * accessible.
 */
function consumeHashTokens(): string | null {
  if (typeof window === 'undefined') return null
  const hash = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : ''
  if (!hash) return null
  const params = new URLSearchParams(hash)
  const access = params.get('access_token')
  const refresh = params.get('refresh_token')
  if (!access) return null
  localStorage.setItem(STORED_TOKEN_KEY, access)
  if (refresh) localStorage.setItem(STORED_REFRESH_KEY, refresh)
  history.replaceState(null, '', window.location.pathname + window.location.search)
  return access
}

const DEV_BYPASS_TOKEN =
  (import.meta.env.VITE_DEV_BYPASS_TOKEN as string | undefined) ??
  (import.meta.env.VITE_E2E_TOKEN as string | undefined) ??
  null

export async function getAccessToken(): Promise<string | null> {
  // 0. Dev bypass — lets the app work on bsserver:port / localhost during
  //    development without going through auth.bsvibe.dev. Only ever used
  //    when VITE_DEV_BYPASS_TOKEN (or VITE_E2E_TOKEN) is explicitly set.
  if (DEV_BYPASS_TOKEN) {
    return DEV_BYPASS_TOKEN
  }

  if (cachedToken && Date.now() < cachedToken.expiresAt - 30_000) {
    return cachedToken.value
  }

  // 1. Hash fragment (just returned from SSO login on a cross-origin host)
  const hashToken = consumeHashTokens()
  if (hashToken && !isExpired(hashToken)) {
    return hashToken
  }

  // 2. localStorage fallback (persisted from a previous hash exchange)
  const stored = typeof window !== 'undefined' ? localStorage.getItem(STORED_TOKEN_KEY) : null
  if (stored && !isExpired(stored)) {
    return stored
  }

  // 3. Cookie-based session (works only on *.bsvibe.dev origins)
  try {
    const res = await fetch(`${AUTH_URL}/api/session`, { credentials: 'include' })
    if (!res.ok) return null
    const data: SessionResponse = await res.json()
    cachedToken = {
      value: data.access_token,
      expiresAt: Date.now() + data.expires_in * 1000,
    }
    return data.access_token
  } catch {
    return null
  }
}

export function clearTokenCache() {
  cachedToken = null
  if (typeof window !== 'undefined') {
    localStorage.removeItem(STORED_TOKEN_KEY)
    localStorage.removeItem(STORED_REFRESH_KEY)
  }
}

function decodeJwt(token: string): Record<string, unknown> {
  const parts = token.split('.')
  let base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
  const pad = base64.length % 4
  if (pad) base64 += '='.repeat(4 - pad)
  return JSON.parse(atob(base64))
}

export function useAuth() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    ;(async () => {
      // Dev bypass — synthesize a user without decoding a real JWT.
      if (DEV_BYPASS_TOKEN) {
        setUser({
          id: 'dev-user',
          email: 'dev@bsnexus.local',
          tenantId: '',
          role: 'admin',
        })
        setLoading(false)
        return
      }
      const token = await getAccessToken()
      if (!token) {
        setLoading(false)
        return
      }
      try {
        const payload = decodeJwt(token) as {
          sub: string
          email: string
          app_metadata?: { tenant_id?: string; role?: string }
        }
        setUser({
          id: payload.sub,
          email: payload.email,
          tenantId: payload.app_metadata?.tenant_id ?? '',
          role: payload.app_metadata?.role ?? 'member',
        })
      } catch {
        // Not a JWT — skip and stay unauthenticated.
      }
      setLoading(false)
    })()
  }, [])

  function login() {
    const redirectUri = `${window.location.origin}/dashboard`
    window.location.href = `${AUTH_URL}/login?redirect_uri=${encodeURIComponent(redirectUri)}`
  }

  async function logout() {
    try {
      await fetch(`${AUTH_URL}/api/session`, { method: 'DELETE', credentials: 'include' })
    } catch {
      /* cross-origin logout best-effort */
    }
    clearTokenCache()
    setUser(null)
    window.location.href = 'https://bsvibe.dev/'
  }

  return { user, loading, login, logout }
}
