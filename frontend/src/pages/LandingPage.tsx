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
    <div className="flex flex-col items-center justify-center min-h-screen bg-bg-primary">
      <div className="text-center space-y-6 max-w-lg px-6">
        {/* Logo */}
        <div className="flex items-center justify-center gap-3 mb-2">
          <div className="bg-accent w-12 h-12 rounded-xl flex items-center justify-center">
            <span className="text-white text-xl font-bold">B</span>
          </div>
        </div>
        <h1 className="text-4xl font-bold text-text-primary">BSNexus</h1>
        <p className="text-lg text-text-secondary">
          AI-Powered Development Manager
        </p>
        <p className="text-sm text-text-tertiary">
          LLM Architect가 프로젝트를 설계하고, 분산 Worker가 자동으로 코드를 작성합니다.
        </p>
        <div className="pt-4">
          {user ? (
            <button
              onClick={() => navigate('/dashboard')}
              className="px-6 py-3 bg-accent text-white rounded-lg hover:bg-accent-light text-base font-medium transition-colors"
            >
              대시보드로 이동
            </button>
          ) : (
            <button
              onClick={handleLogin}
              className="px-6 py-3 bg-accent text-white rounded-lg hover:bg-accent-light text-base font-medium transition-colors"
            >
              로그인
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
