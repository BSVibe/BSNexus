import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

const BSVIBE_AUTH_URL = import.meta.env.VITE_BSVIBE_AUTH_URL || 'https://auth.bsvibe.dev'
const API_URL = import.meta.env.VITE_API_URL || window.location.origin

function handleLogin() {
  const callbackUrl = `${API_URL}/auth/callback`
  const state = Math.random().toString(36).substring(2) + Date.now().toString(36)
  sessionStorage.setItem('auth_state', state)
  window.location.href = `${BSVIBE_AUTH_URL}/login?redirect_uri=${encodeURIComponent(callbackUrl)}&state=${encodeURIComponent(state)}`
}

export default function LandingPage() {
  const user = useAuthStore((s) => s.user)
  const navigate = useNavigate()

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-50">
      <div className="text-center space-y-6 max-w-lg px-6">
        <h1 className="text-4xl font-bold text-gray-900">BSNexus</h1>
        <p className="text-lg text-gray-600">
          AI-Powered Development Manager
        </p>
        <p className="text-sm text-gray-500">
          LLM Architect가 프로젝트를 설계하고, 분산 Worker가 자동으로 코드를 작성합니다.
        </p>
        <div className="pt-4">
          {user ? (
            <button
              onClick={() => navigate('/dashboard')}
              className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-base font-medium"
            >
              대시보드로 이동
            </button>
          ) : (
            <button
              onClick={handleLogin}
              className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-base font-medium"
            >
              로그인
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
