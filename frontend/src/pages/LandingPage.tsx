import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { Cpu, LayoutGrid, Network } from 'lucide-react'

const BSVIBE_AUTH_URL = import.meta.env.VITE_BSVIBE_AUTH_URL || 'https://auth.bsvibe.dev'

function generateSecureState(): string {
  const buffer = new Uint8Array(32)
  crypto.getRandomValues(buffer)
  return Array.from(buffer, (b) => b.toString(16).padStart(2, '0')).join('')
}

function handleLogin() {
  const callbackUrl = `${window.location.origin}/auth/callback`
  const state = generateSecureState()
  sessionStorage.setItem('auth_state', state)
  window.location.href = `${BSVIBE_AUTH_URL}/login?redirect_uri=${encodeURIComponent(callbackUrl)}&state=${encodeURIComponent(state)}`
}

const features = [
  {
    icon: Cpu,
    title: 'Project Architect',
    description: 'AI가 대화를 통해 프로젝트를 설계하고 태스크를 분해합니다.',
  },
  {
    icon: LayoutGrid,
    title: 'Task Kanban',
    description: '실시간 칸반 보드로 태스크 상태를 추적하고 관리합니다.',
  },
  {
    icon: Network,
    title: 'Distributed Workers',
    description: '분산 Worker 노드가 자동으로 코드를 작성하고 실행합니다.',
  },
]

export default function LandingPage() {
  const user = useAuthStore((s) => s.user)
  const navigate = useNavigate()

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-bg-primary relative overflow-hidden">
      {/* Background gradient glow */}
      <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] rounded-full pointer-events-none"
        style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.08) 0%, rgba(59,130,246,0.02) 40%, transparent 70%)' }}
      />

      {/* Main card */}
      <div className="relative z-10 w-full max-w-xl mx-auto px-6">
        <div className="bg-bg-card border border-border rounded-xl p-10 shadow-2xl shadow-black/40">
          {/* Logo + Title */}
          <div className="flex flex-col items-center mb-8">
            <div className="bg-accent w-14 h-14 rounded-xl flex items-center justify-center shadow-lg shadow-accent/20 mb-5">
              <svg width="28" height="28" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="14" cy="14" r="3" fill="white" />
                <circle cx="6" cy="6" r="2" fill="white" opacity="0.7" />
                <circle cx="22" cy="6" r="2" fill="white" opacity="0.7" />
                <circle cx="6" cy="22" r="2" fill="white" opacity="0.7" />
                <circle cx="22" cy="22" r="2" fill="white" opacity="0.7" />
                <line x1="14" y1="14" x2="6" y2="6" stroke="white" strokeWidth="1.2" opacity="0.5" />
                <line x1="14" y1="14" x2="22" y2="6" stroke="white" strokeWidth="1.2" opacity="0.5" />
                <line x1="14" y1="14" x2="6" y2="22" stroke="white" strokeWidth="1.2" opacity="0.5" />
                <line x1="14" y1="14" x2="22" y2="22" stroke="white" strokeWidth="1.2" opacity="0.5" />
              </svg>
            </div>
            <h1 className="text-4xl font-bold text-text-primary tracking-tight">BSNexus</h1>
            <p className="text-text-secondary mt-2 text-center text-base">
              Orchestrate AI agents, from design to deployment.
            </p>
          </div>

          {/* Features */}
          <div className="grid grid-cols-3 gap-4 mb-8">
            {features.map((f) => (
              <div key={f.title} className="flex flex-col items-center text-center p-3 rounded-lg bg-bg-elevated/50">
                <div className="w-10 h-10 rounded-lg bg-accent/10 flex items-center justify-center mb-2">
                  <f.icon className="w-5 h-5 text-accent-text" />
                </div>
                <span className="text-sm font-medium text-text-primary mb-1">{f.title}</span>
                <span className="text-xs text-text-tertiary leading-relaxed">{f.description}</span>
              </div>
            ))}
          </div>

          {/* CTA Button */}
          <div className="flex flex-col items-center">
            {user ? (
              <button
                onClick={() => navigate('/dashboard')}
                className="w-full py-3 bg-accent text-white rounded-lg hover:bg-accent-light text-base font-semibold transition-all shadow-lg shadow-accent/25 hover:shadow-accent/40"
              >
                Go to Dashboard
              </button>
            ) : (
              <button
                onClick={handleLogin}
                className="w-full py-3 bg-accent text-white rounded-lg hover:bg-accent-light text-base font-semibold transition-all shadow-lg shadow-accent/25 hover:shadow-accent/40"
              >
                Sign in with BSVibe
              </button>
            )}
          </div>
        </div>

        {/* Footer */}
        <p className="text-center text-text-muted text-xs mt-6">
          Powered by BSVibe
        </p>
      </div>
    </div>
  )
}
