import axios from 'axios'
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
      window.location.href = `${AUTH_URL}/login`
    }
    return Promise.reject(error)
  },
)

export default apiClient
