import { create } from 'zustand'
import axios from 'axios'
import { API_BASE_URL } from '../api/client'
const TOKEN_KEY = 'bsnexus_access_token'
const REFRESH_KEY = 'bsnexus_refresh_token'

interface AuthUser {
  id: string
  email: string | null
  role: string | null
  app_metadata: Record<string, unknown> | null
}

interface AuthState {
  user: AuthUser | null
  accessToken: string | null
  refreshToken: string | null
  isLoading: boolean
  handleCallback: (accessToken: string, refreshToken: string) => Promise<void>
  signOut: () => Promise<void>
  refresh: () => Promise<boolean>
  initialize: () => Promise<void>
  clear: () => void
}

function persistTokens(access: string | null, refresh: string | null) {
  if (access) {
    localStorage.setItem(TOKEN_KEY, access)
  } else {
    localStorage.removeItem(TOKEN_KEY)
  }
  if (refresh) {
    localStorage.setItem(REFRESH_KEY, refresh)
  } else {
    localStorage.removeItem(REFRESH_KEY)
  }
}

function loadTokens(): { access: string | null; refresh: string | null } {
  return {
    access: localStorage.getItem(TOKEN_KEY),
    refresh: localStorage.getItem(REFRESH_KEY),
  }
}

async function fetchUser(accessToken: string): Promise<AuthUser> {
  const { data } = await axios.get(`${API_BASE_URL}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  return data
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  refreshToken: null,
  isLoading: true,

  handleCallback: async (accessToken: string, refreshToken: string) => {
    persistTokens(accessToken, refreshToken)
    set({ accessToken, refreshToken, isLoading: false })

    try {
      const user = await fetchUser(accessToken)
      set({ user })
    } catch {
      // User fetch is best-effort — tokens are already stored
    }
  },

  signOut: async () => {
    const token = get().accessToken
    try {
      if (token) {
        await axios.post(`${API_BASE_URL}/api/v1/auth/logout`, null, {
          headers: { Authorization: `Bearer ${token}` },
        })
      }
    } catch {
      // best-effort
    }
    persistTokens(null, null)
    set({ user: null, accessToken: null, refreshToken: null })
  },

  refresh: async () => {
    const rt = get().refreshToken
    if (!rt) return false
    try {
      const { data } = await axios.post(`${API_BASE_URL}/api/v1/auth/refresh`, { refresh_token: rt })
      persistTokens(data.access_token, data.refresh_token)
      set({ accessToken: data.access_token, refreshToken: data.refresh_token })
      return true
    } catch {
      return false
    }
  },

  initialize: async () => {
    const { access, refresh } = loadTokens()
    if (!access) {
      set({ isLoading: false })
      return
    }

    set({ accessToken: access, refreshToken: refresh })

    try {
      const user = await fetchUser(access)
      set({ user, isLoading: false })
    } catch {
      // fetchUser failed — keep tokens (server may be unreachable or JWT config differs)
      // User will still be "authenticated" based on stored token
      set({ isLoading: false })
    }
  },

  clear: () => {
    persistTokens(null, null)
    set({ user: null, accessToken: null, refreshToken: null })
  },
}))
