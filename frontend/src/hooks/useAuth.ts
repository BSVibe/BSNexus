/**
 * BSNexus auth hook — Phase A Batch 5 status.
 *
 * Lockin §A1-A2: ``@bsvibe/auth`` is the canonical extraction target.
 * That package was published in ``bsvibe-frontend-lib`` (PR
 * https://github.com/BSVibe/bsvibe-frontend-lib) and its
 * ``UseAuthValue`` exposes a richer multi-tenant shape:
 *
 *   { user, tenants, activeTenant, hasPermission, switchTenant,
 *     refresh, isLoading, error }
 *
 * BSNexus today uses a simpler 4-prop shape (``user``, ``loading``,
 * ``login``, ``logout``) consumed by all four founder-metaphor surfaces
 * (Direction / Progress / Decisions / Inside) plus Sidebar +
 * ProtectedRoute + LandingPage.
 *
 * The full swap to ``@bsvibe/auth`` is gated on:
 *  1. Lockin §A0 #12 — user-action GitHub Packages PAT + Vercel
 *     ``NPM_TOKEN`` so ``@bsvibe/*`` packages resolve.
 *  2. A consumer migration that maps BSNexus's
 *     ``login`` / ``logout`` / ``loading`` props onto
 *     ``@bsvibe/auth``'s ``isLoading`` + (BSNexus-side) login/logout
 *     helpers (the multi-tenant hook intentionally leaves redirect
 *     orchestration to consumers — Auth_Design.md §5).
 *
 * Until then this file remains the production hook. New code should
 * read ``user.email`` and gate via the AuthContext consumer pattern in
 * ``components/auth/AuthContext.ts`` so the eventual swap is
 * mechanical.
 */
import { useEffect, useState } from 'react'
import { isDemoMode } from '@bsvibe/demo'

interface User {
  id: string
  email: string
  tenantId: string
  tenantName: string | null
  role: string
}

// ``NEXT_PUBLIC_AUTH_URL`` is the canonical Next.js form; ``VITE_AUTH_URL``
// is accepted as a fallback so the auth integration stays usable across
// Phase Z transition without forcing every consumer to flip envs in
// lockstep.
export const AUTH_URL =
  process.env.NEXT_PUBLIC_AUTH_URL ||
  process.env.VITE_AUTH_URL ||
  'https://auth.bsvibe.dev'

// LocalStorage keys for non-cookie-SSO environments (local dev, Tailscale, etc.)
const LS_ACCESS_TOKEN = 'bsnexus_access_token'
const LS_REFRESH_TOKEN = 'bsnexus_refresh_token'
const LS_EXPIRES_AT = 'bsnexus_expires_at'

interface SessionTenant {
  id: string
  name: string
  role?: string
}

interface SessionResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  tenants?: SessionTenant[]
  active_tenant_id?: string
}

let cachedToken: { value: string; expiresAt: number } | null = null

interface AccessTokenOptions {
  probeRemoteSession?: boolean
}

function loadTokenFromLocalStorage(): { value: string; expiresAt: number } | null {
  const value = localStorage.getItem(LS_ACCESS_TOKEN)
  const expiresAtStr = localStorage.getItem(LS_EXPIRES_AT)
  if (!value || !expiresAtStr) return null
  const expiresAt = Number(expiresAtStr)
  if (!Number.isFinite(expiresAt)) return null
  return { value, expiresAt }
}

function saveTokenToLocalStorage(
  accessToken: string,
  refreshToken: string,
  expiresIn: number,
): void {
  const expiresAt = Date.now() + expiresIn * 1000
  localStorage.setItem(LS_ACCESS_TOKEN, accessToken)
  localStorage.setItem(LS_REFRESH_TOKEN, refreshToken)
  localStorage.setItem(LS_EXPIRES_AT, String(expiresAt))
}

function clearLocalStorageTokens(): void {
  localStorage.removeItem(LS_ACCESS_TOKEN)
  localStorage.removeItem(LS_REFRESH_TOKEN)
  localStorage.removeItem(LS_EXPIRES_AT)
}

export async function getAccessToken({
  probeRemoteSession = true,
}: AccessTokenOptions = {}): Promise<string | null> {
  if (cachedToken && Date.now() < cachedToken.expiresAt - 30_000) {
    return cachedToken.value
  }

  // Non-cookie-SSO fallback: token stashed in localStorage by auth callback.
  const stored = loadTokenFromLocalStorage()
  if (stored && Date.now() < stored.expiresAt - 30_000) {
    cachedToken = stored
    return stored.value
  }

  if (!probeRemoteSession) {
    return null
  }

  // Production path: cross-subdomain cookie SSO via auth.bsvibe.dev.
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
  clearLocalStorageTokens()
}

/**
 * Inject a demo session JWT into the auth token cache so getAccessToken()
 * returns the demo Bearer for every fetch — without this, the demo shell
 * loads but every data fetch goes out unauth'd.
 *
 * Wire from DemoModeProvider via @bsvibe/demo 0.3 onSessionReady.
 */
export function injectDemoToken(token: string, expiresIn: number): void {
  const expiresAt = Date.now() + expiresIn * 1000
  cachedToken = { value: token, expiresAt }
  if (typeof window !== 'undefined') {
    saveTokenToLocalStorage(token, '', expiresIn)
  }
}

/**
 * Parse tokens from the OAuth callback URL fragment and persist them.
 * Call from the app root on mount when `window.location.hash` starts with
 * `#/auth/callback`. Returns true if a token was found and stored.
 */
export function consumeAuthCallback(): boolean {
  // Hash looks like: "#/auth/callback#access_token=...&refresh_token=...&expires_in=..."
  // after the route hash, tokens are in a second fragment. We also support
  // the plain "?access_token=..." query form as a fallback.
  const raw = window.location.hash || ''
  const queryRaw = window.location.search || ''
  const tokenPart = raw.includes('access_token=')
    ? raw.slice(raw.indexOf('access_token='))
    : queryRaw.includes('access_token=')
      ? queryRaw.slice(queryRaw.indexOf('access_token='))
      : ''
  if (!tokenPart) return false
  const params = new URLSearchParams(tokenPart)
  const accessToken = params.get('access_token')
  const refreshToken = params.get('refresh_token') ?? ''
  const expiresIn = Number(params.get('expires_in') ?? '3600')
  if (!accessToken) return false
  saveTokenToLocalStorage(accessToken, refreshToken, expiresIn)
  cachedToken = {
    value: accessToken,
    expiresAt: Date.now() + expiresIn * 1000,
  }
  return true
}

function decodeJwt(token: string): Record<string, unknown> {
  const parts = token.split('.')
  let base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
  const pad = base64.length % 4
  if (pad) base64 += '='.repeat(4 - pad)
  return JSON.parse(atob(base64))
}

export function useAuth({
  probeRemoteSession = true,
}: AccessTokenOptions = {}) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [tenants, setTenants] = useState<SessionTenant[]>([])

  useEffect(() => {
    // If we just returned from auth.bsvibe.dev with tokens in the hash,
    // stash them in localStorage and clean the URL before anything else
    // reads location.
    if (
      typeof window !== 'undefined' &&
      window.location.hash.startsWith('#/auth/callback') &&
      consumeAuthCallback()
    ) {
      const landing = window.location.pathname === '/' ? '/dashboard' : window.location.pathname
      window.history.replaceState(null, '', landing)
    }
    ;(async () => {
      const token = await getAccessToken({ probeRemoteSession })
      if (!token) {
        setLoading(false)
        return
      }
      try {
        const payload = decodeJwt(token) as {
          sub?: string
          email?: string
          tenant_id?: string
          is_demo?: boolean
          app_metadata?: { tenant_id?: string; role?: string }
        }
        const isDemoSession = payload.is_demo === true
        // Demo JWT carries `tenant_id` directly; prod JWT wraps it in
        // app_metadata. Fall back from one to the other.
        const tenantId =
          payload.app_metadata?.tenant_id ?? payload.tenant_id ?? ''
        let tenantName: string | null = isDemoSession ? 'Demo sandbox' : null
        let tenantList: SessionTenant[] = []
        let activeTenantId: string = tenantId
        // Skip the prod tenants probe in demo mode — auth.bsvibe.dev
        // does not allow the demo origin and the call CORS-fails.
        if (!isDemoSession && !isDemoMode()) {
          try {
            const res = await fetch(`${AUTH_URL}/api/session`, {
              credentials: 'include',
              headers: { Authorization: `Bearer ${token}` },
            })
            if (res.ok) {
              const data: SessionResponse = await res.json()
              tenantList = data.tenants ?? []
              activeTenantId = data.active_tenant_id ?? tenantId
              tenantName =
                tenantList.find((t) => t.id === activeTenantId)?.name ?? null
            }
          } catch {
            // ignore
          }
        }
        setTenants(tenantList)
        setUser({
          id: payload.sub ?? (isDemoSession ? 'demo-user' : ''),
          email: payload.email ?? (isDemoSession ? 'demo@bsvibe.dev' : ''),
          tenantId: activeTenantId,
          tenantName,
          role: payload.app_metadata?.role ?? (isDemoSession ? 'demo' : 'member'),
        })
      } catch {
        // Not a JWT — skip and stay unauthenticated.
      }
      setLoading(false)
    })()
  }, [probeRemoteSession])

  // Switch active workspace via /api/session/switch_tenant. The endpoint
  // sets a server-side cookie + writes new active_tenant_id; reload so
  // every consumer (frontend + backend) picks up the new context.
  async function switchTenant(nextTenantId: string): Promise<void> {
    if (nextTenantId === user?.tenantId) return
    const token = await getAccessToken({ probeRemoteSession })
    if (!token) return
    try {
      const res = await fetch(`${AUTH_URL}/api/session/switch_tenant`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ tenant_id: nextTenantId }),
      })
      if (res.ok) {
        clearTokenCache()
        window.location.reload()
      }
    } catch {
      // ignore
    }
  }

  function callbackUrl(): string {
    // Hash-route callback so the auth service's redirect-allowlist match
    // only needs to care about origin; token fragment lands as a second
    // hash that `consumeAuthCallback()` parses.
    return `${window.location.origin}/#/auth/callback`
  }

  function login() {
    const redirect = encodeURIComponent(callbackUrl())
    window.location.href = `${AUTH_URL}/login?redirect_uri=${redirect}`
  }

  function signup() {
    const redirect = encodeURIComponent(callbackUrl())
    window.location.href = `${AUTH_URL}/signup?redirect_uri=${redirect}`
  }

  async function logout() {
    try {
      await fetch(`${AUTH_URL}/api/session`, { method: 'DELETE', credentials: 'include' })
    } catch {
      /* cross-origin logout best-effort */
    }
    clearTokenCache()
    setUser(null)
    window.location.href = '/'
  }

  return { user, loading, login, signup, logout, tenants, switchTenant }
}
