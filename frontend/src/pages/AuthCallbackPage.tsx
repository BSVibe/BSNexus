import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

export default function AuthCallbackPage() {
  const navigate = useNavigate()
  const handleCallback = useAuthStore((s) => s.handleCallback)

  useEffect(() => {
    const hash = window.location.hash.substring(1)
    const params = new URLSearchParams(hash)
    const accessToken = params.get('access_token')
    const refreshToken = params.get('refresh_token')

    // Validate CSRF state parameter
    const returnedState = params.get('state')
    const savedState = sessionStorage.getItem('auth_state')
    sessionStorage.removeItem('auth_state')

    if (!returnedState || !savedState || returnedState !== savedState) {
      navigate('/', { replace: true })
      return
    }

    if (accessToken && refreshToken) {
      handleCallback(accessToken, refreshToken)
        .then(() => navigate('/dashboard', { replace: true }))
        .catch(() => navigate('/', { replace: true }))
    } else {
      navigate('/', { replace: true })
    }
  }, [handleCallback, navigate])

  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-accent" />
    </div>
  )
}
