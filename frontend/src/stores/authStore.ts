import { create } from 'zustand'
import axios from 'axios'

const BASE = import.meta.env.VITE_API_URL || ''

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

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  refreshToken: null,
  isLoading: true,

  handleCallback: async (accessToken: string, refreshToken: string) => {
    set({ accessToken, refreshToken })

    const { data: user } = await axios.get(`${BASE}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    set({ user })
  },

  signOut: async () => {
    const token = get().accessToken
    try {
      if (token) {
        await axios.post(`${BASE}/api/v1/auth/logout`, null, {
          headers: { Authorization: `Bearer ${token}` },
        })
      }
    } catch {
      // best-effort
    }
    set({ user: null, accessToken: null, refreshToken: null })
  },

  refresh: async () => {
    const rt = get().refreshToken
    if (!rt) return false
    try {
      const { data } = await axios.post(`${BASE}/api/v1/auth/refresh`, { refresh_token: rt })
      set({ accessToken: data.access_token, refreshToken: data.refresh_token })
      return true
    } catch {
      return false
    }
  },

  initialize: async () => {
    set({ isLoading: false })
  },

  clear: () => {
    set({ user: null, accessToken: null, refreshToken: null })
  },
}))
