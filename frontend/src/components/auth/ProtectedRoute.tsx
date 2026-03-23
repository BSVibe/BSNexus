import { useEffect } from 'react'
import { useAuthStore } from '../../stores/authStore'

const BSVIBE_AUTH_URL = import.meta.env.VITE_BSVIBE_AUTH_URL || 'https://auth.bsvibe.dev'
const API_URL = import.meta.env.VITE_API_URL || window.location.origin

function buildLoginUrl(): string {
  const callbackUrl = `${API_URL}/auth/callback`
  const state = crypto.randomUUID()
  sessionStorage.setItem('auth_state', state)
  return `${BSVIBE_AUTH_URL}/login?redirect_uri=${encodeURIComponent(callbackUrl)}&state=${encodeURIComponent(state)}`
}

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)

  useEffect(() => {
    if (!user) {
      window.location.href = buildLoginUrl()
    }
  }, [user])

  if (!user) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    )
  }

  return <>{children}</>
}
