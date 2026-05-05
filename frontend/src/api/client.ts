import axios from 'axios'
import { isDemoMode } from '@bsvibe/demo'
import { AUTH_URL, getAccessToken, clearTokenCache } from '../hooks/useAuth'

// ``NEXT_PUBLIC_API_URL`` (Next.js) is the canonical client-visible env;
// the legacy ``VITE_API_URL`` form is accepted as a fallback so existing
// .env files keep working during the Phase Z transition.
export const API_BASE_URL: string =
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.VITE_API_URL ||
  ''

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300_000,
  // Demo deployments authenticate via a HttpOnly cookie set by
  // POST /api/v1/demo/session — the prod Bearer-token path is unused.
  withCredentials: isDemoMode(),
  headers: {
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.request.use(async (config) => {
  const token = await getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true
      clearTokenCache()
      // In demo deployments, BSVibe Auth is not the issuer — letting
      // the interceptor bounce to auth.bsvibe.dev/login on a 401 (e.g.
      // before the demo session cookie is established) breaks the demo
      // shell. Surface the error so callers can show in-app fallback
      // UI instead.
      if (!isDemoMode()) {
        window.location.href = `${AUTH_URL}/login`
      }
    }
    return Promise.reject(error)
  },
)

export default apiClient
