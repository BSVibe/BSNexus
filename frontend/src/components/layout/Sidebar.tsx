import { useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { SettingsModal } from './SettingsModal'
import { useAuthStore } from '../../stores/authStore'

const navItems = [
  { to: '/dashboard', label: 'Projects', icon: 'folder_open' },
  { to: '/architect', label: 'Architect', icon: 'architecture' },
]

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function Sidebar({ isOpen, onClose }: SidebarProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const signOut = useAuthStore((s) => s.signOut)
  const user = useAuthStore((s) => s.user)

  const handleLogout = async () => {
    await signOut()
    navigate('/')
  }

  const isActive = (to: string) => {
    if (to === '/dashboard') return location.pathname === '/dashboard' || location.pathname.startsWith('/projects/')
    return location.pathname.startsWith(to)
  }

  return (
    <>
      {/* Backdrop - mobile only */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 md:hidden"
          onClick={onClose}
        />
      )}
    <aside className={`fixed left-0 top-0 w-64 bg-stitch-surface-low h-screen flex flex-col py-6 px-4 z-50 transform transition-transform duration-200 ${isOpen ? 'translate-x-0' : '-translate-x-full'} md:translate-x-0 md:static md:z-auto md:shrink-0`}>
      {/* Logo area */}
      <div className="mb-10 px-2 flex items-center gap-3">
        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-stitch-primary to-stitch-primary-container flex items-center justify-center">
          <span className="material-symbols-outlined text-stitch-on-primary-container" style={{ fontVariationSettings: "'FILL' 1" }}>
            architecture
          </span>
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-[-0.04em] text-accent-text">BSNexus</h1>
          <p className="text-[10px] uppercase tracking-widest text-text-secondary font-bold">Agent Orchestrator</p>
        </div>
      </div>


      {/* Navigation */}
      <nav className="flex-1 space-y-1">
        {navItems.map((item) => {
          const active = isActive(item.to)

          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/dashboard'}
              onClick={onClose}
              className={`flex items-center gap-3 px-3 py-2.5 text-sm tracking-tight transition-all duration-200 rounded-lg ${
                active
                  ? 'text-stitch-primary font-semibold bg-stitch-surface-highest shadow-[0_0_15px_rgba(133,173,255,0.1)]'
                  : 'text-text-secondary font-medium hover:text-accent-text hover:bg-stitch-surface-container'
              }`}
            >
              <span
                className="material-symbols-outlined"
                style={active ? { fontVariationSettings: "'FILL' 1" } : undefined}
              >
                {item.icon}
              </span>
              {item.label}
            </NavLink>
          )
        })}

        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          className="flex items-center gap-3 px-3 py-2.5 text-sm text-text-secondary font-medium hover:text-accent-text hover:bg-stitch-surface-container rounded-lg cursor-pointer transition-colors w-full"
        >
          <span className="material-symbols-outlined">settings</span>
          Settings
        </button>
      </nav>

      {/* Bottom section: user profile */}
      <div className="mt-auto p-3 bg-stitch-surface-container rounded-xl flex items-center gap-3">
        <div className="w-10 h-10 rounded-full bg-stitch-surface-highest flex items-center justify-center shrink-0">
          <span className="material-symbols-outlined text-text-secondary" style={{ fontSize: '20px' }}>person</span>
        </div>
        <div className="overflow-hidden flex-1 min-w-0">
          <p className="text-sm font-bold truncate text-text-primary">
            {user?.email?.split('@')[0] || 'User'}
          </p>
          <p className="text-xs text-text-secondary truncate">{user?.email || ''}</p>
        </div>
        <button
          type="button"
          onClick={handleLogout}
          className="text-text-tertiary hover:text-stitch-error transition-colors shrink-0"
          title="Logout"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>logout</span>
        </button>
      </div>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </aside>
    </>
  )
}
