import axios from 'axios'
import { getAccessToken, clearTokenCache } from '../hooks/useAuth'

export const API_BASE_URL: string = import.meta.env.VITE_API_URL || ''

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
      window.location.href = 'https://auth.bsvibe.dev/login'
    }
    return Promise.reject(error)
  },
)

export default apiClient
