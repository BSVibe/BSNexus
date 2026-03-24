import axios from 'axios'
import { useAuthStore } from '../stores/authStore'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  timeout: 120_000,
  headers: {
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken
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
      const refreshed = await useAuthStore.getState().refresh()
      if (refreshed) {
        originalRequest.headers.Authorization = `Bearer ${useAuthStore.getState().accessToken}`
        return apiClient(originalRequest)
      }
      useAuthStore.getState().clear()
    }
    return Promise.reject(error)
  },
)

export default apiClient
