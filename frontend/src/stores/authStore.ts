import { create } from 'zustand'
import axios from 'axios'
import { BSVibeAuth } from '../lib/bsvibe-auth'
import type { BSVibeUser } from '../lib/bsvibe-auth'
import { API_BASE_URL } from '../api/client'

const BSVIBE_AUTH_URL = import.meta.env.VITE_BSVIBE_AUTH_URL || 'https://auth.bsvibe.dev'

const auth = new BSVibeAuth({
  authUrl: BSVIBE_AUTH_URL,
  callbackPath: '/auth/callback',
})

interface AuthUser {
  id: string
  email: string | null
  role: string | null
  app_metadata: Record<string, unknown> | null
}

interface AuthState {
  user: AuthUser | null
  accessToken: string | null
  isLoading: boolean
  handleCallback: () => void
  signOut: () => void
  login: () => void
  signup: () => void
  initialize: () => void
  getToken: () => string | null
}

function userFromBSVibeUser(bsUser: BSVibeUser): AuthUser {
  return {
    id: bsUser.id,
    email: bsUser.email,
    role: bsUser.role,
    app_metadata: { tenant_id: bsUser.tenantId },
  }
}

async function enrichUser(accessToken: string): Promise<AuthUser | null> {
  try {
    const { data } = await axios.get(`${API_BASE_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    return data
  } catch {
    return null
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  isLoading: true,

  handleCallback: () => {
    const bsUser = auth.handleCallback()
    if (!bsUser) return

    const user = userFromBSVibeUser(bsUser)
    set({ user, accessToken: bsUser.accessToken, isLoading: false })

    // Best-effort enrichment from backend
    enrichUser(bsUser.accessToken).then((enriched) => {
      if (enriched) set({ user: enriched })
    })
  },

  signOut: () => {
    set({ user: null, accessToken: null })
    auth.logout()
  },

  login: () => {
    auth.redirectToLogin()
  },

  signup: () => {
    auth.redirectToSignup()
  },

  initialize: () => {
    // 1. Check local session first
    const localUser = auth.getUser()
    if (localUser) {
      set({
        user: userFromBSVibeUser(localUser),
        accessToken: localUser.accessToken,
        isLoading: false,
      })
      // Best-effort enrichment
      enrichUser(localUser.accessToken).then((enriched) => {
        if (enriched) set({ user: enriched })
      })
      return
    }

    // 2. Silent SSO check (redirect-based)
    const result = auth.checkSession()
    if (result === 'redirect') return // page is navigating away
    if (result) {
      set({
        user: userFromBSVibeUser(result),
        accessToken: result.accessToken,
        isLoading: false,
      })
      enrichUser(result.accessToken).then((enriched) => {
        if (enriched) set({ user: enriched })
      })
      return
    }

    set({ isLoading: false })
  },

  getToken: () => {
    return auth.getToken()
  },
}))
