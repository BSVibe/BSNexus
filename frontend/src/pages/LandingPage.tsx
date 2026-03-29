import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

const BSVIBE_AUTH_URL = import.meta.env.VITE_BSVIBE_AUTH_URL || 'https://auth.bsvibe.dev'

function handleLogin() {
  const callbackUrl = `${window.location.origin}/auth/callback`
  const state = Math.random().toString(36).substring(2) + Date.now().toString(36)
  sessionStorage.setItem('auth_state', state)
  window.location.href = `${BSVIBE_AUTH_URL}/login?redirect_uri=${encodeURIComponent(callbackUrl)}&state=${encodeURIComponent(state)}`
}

export default function LandingPage() {
  const user = useAuthStore((s) => s.user)
  const navigate = useNavigate()

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-bg-primary relative overflow-hidden">
      {/* Background glow */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-accent/5 rounded-full blur-[120px] pointer-events-none" />

      <div className="text-center space-y-8 max-w-lg px-6 relative z-10">
        {/* Logo */}
        <div className="flex items-center justify-center gap-3 mb-4">
          <div className="bg-accent w-14 h-14 rounded-2xl flex items-center justify-center shadow-lg shadow-accent/20">
            <span className="text-white text-2xl font-bold">B</span>
          </div>
        </div>
        <h1 className="text-5xl font-bold text-text-primary tracking-tight">BSNexus</h1>
        <p className="text-lg text-text-secondary">
          AI-Powered Development Manager
        </p>
        <p className="text-sm text-text-tertiary leading-relaxed max-w-sm mx-auto">
          LLM Architect가 프로젝트를 설계하고, 분산 Worker가 자동으로 코드를 작성합니다.
        </p>
        <div className="pt-2">
          {user ? (
            <button
              onClick={() => navigate('/dashboard')}
              className="px-8 py-3.5 bg-accent text-white rounded-xl hover:bg-accent-light text-base font-semibold transition-all shadow-lg shadow-accent/25 hover:shadow-accent/40 hover:-translate-y-0.5"
            >
              대시보드로 이동
            </button>
          ) : (
            <button
              onClick={handleLogin}
              className="px-8 py-3.5 bg-accent text-white rounded-xl hover:bg-accent-light text-base font-semibold transition-all shadow-lg shadow-accent/25 hover:shadow-accent/40 hover:-translate-y-0.5"
            >
              로그인
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
