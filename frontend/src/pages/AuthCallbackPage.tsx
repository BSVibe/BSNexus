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

    if (accessToken && refreshToken) {
      handleCallback(accessToken, refreshToken).then(() => {
        navigate('/dashboard', { replace: true })
      })
    } else {
      navigate('/dashboard', { replace: true })
    }
  }, [handleCallback, navigate])

  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
    </div>
  )
}
