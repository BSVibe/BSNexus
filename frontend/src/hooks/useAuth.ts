import { useEffect, useState } from 'react'

interface User {
  id: string
  email: string
  tenantId: string
  role: string
}

const AUTH_URL = 'https://auth.bsvibe.dev'

interface SessionResponse {
  access_token: string
  refresh_token: string
  expires_in: number
}

let cachedToken: { value: string; expiresAt: number } | null = null

export async function getAccessToken(): Promise<string | null> {
  if (cachedToken && Date.now() < cachedToken.expiresAt - 30_000) {
    return cachedToken.value
  }
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
      const token = await getAccessToken()
      if (!token) {
        setLoading(false)
        return
      }
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
      setLoading(false)
    })()
  }, [])

  function login() {
    window.location.href = `${AUTH_URL}/login`
  }

  async function logout() {
    await fetch(`${AUTH_URL}/api/session`, { method: 'DELETE', credentials: 'include' })
    clearTokenCache()
    setUser(null)
    window.location.href = 'https://bsvibe.dev/'
  }

  return { user, loading, login, logout }
}
