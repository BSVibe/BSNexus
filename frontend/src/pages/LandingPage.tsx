import { useNavigate } from 'react-router-dom'
import { useAuthContext } from '../components/auth/AuthContext'

const features = [
  {
    icon: 'psychology',
    title: 'Project Architect',
    description: 'AI designs projects through conversation and decomposes them into tasks.',
  },
  {
    icon: 'view_kanban',
    title: 'Task Kanban',
    description: 'Real-time kanban board to track and manage task states.',
  },
  {
    icon: 'hub',
    title: 'Distributed Workers',
    description: 'Distributed worker nodes automatically write and execute code.',
  },
]

export default function LandingPage() {
  const { user, login } = useAuthContext()
  const navigate = useNavigate()

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-stitch-surface relative overflow-hidden">
      {/* Background gradient glow */}
      <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] rounded-full pointer-events-none"
        style={{ background: 'radial-gradient(circle, rgba(77,142,255,0.08) 0%, rgba(77,142,255,0.02) 40%, transparent 70%)' }}
      />

      {/* Main card */}
      <div className="relative z-10 w-full max-w-xl mx-auto px-6">
        <div className="bg-stitch-surface-container border border-stitch-outline-variant/10 rounded-xl p-10 shadow-2xl shadow-black/40">
          {/* Logo + Title */}
          <div className="flex flex-col items-center mb-8">
            <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-stitch-primary to-stitch-primary-container flex items-center justify-center shadow-lg shadow-stitch-primary/20 mb-5">
              <span className="material-symbols-outlined text-stitch-on-primary-container text-3xl" style={{ fontVariationSettings: "'FILL' 1" }}>
                architecture
              </span>
            </div>
            <h1 className="text-4xl font-extrabold text-white tracking-[-0.04em]">BSNexus</h1>
            <p className="text-text-secondary mt-2 text-center text-base">
              Orchestrate AI agents, from design to deployment.
            </p>
          </div>

          {/* Features */}
          <div className="grid grid-cols-3 gap-4 mb-8">
            {features.map((f) => (
              <div key={f.title} className="flex flex-col items-center text-center p-3 rounded-lg bg-stitch-surface-low">
                <div className="w-10 h-10 rounded-lg bg-stitch-primary/10 flex items-center justify-center mb-2">
                  <span className="material-symbols-outlined text-stitch-primary" style={{ fontSize: '20px' }}>{f.icon}</span>
                </div>
                <span className="text-sm font-medium text-white mb-1">{f.title}</span>
                <span className="text-xs text-text-tertiary leading-relaxed">{f.description}</span>
              </div>
            ))}
          </div>

          {/* CTA Button */}
          <div className="flex flex-col items-center">
            {user ? (
              <button
                onClick={() => navigate('/dashboard')}
                className="w-full py-3 bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container rounded-lg text-base font-bold transition-all shadow-lg shadow-stitch-primary/25 hover:opacity-90"
              >
                Go to Dashboard
              </button>
            ) : (
              <>
                <button
                  onClick={login}
                  className="w-full py-3 bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container rounded-lg text-base font-bold transition-all shadow-lg shadow-stitch-primary/25 hover:opacity-90"
                >
                  Sign in with BSVibe
                </button>
                <p className="text-center text-sm text-text-secondary mt-4">
                  Don't have an account?{' '}
                  <button
                    onClick={login}
                    className="text-stitch-primary hover:text-stitch-primary-container font-medium transition-colors"
                  >
                    Sign up
                  </button>
                </p>
              </>
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
