import { NavLink, useLocation } from 'react-router-dom'
import { useAuthContext } from '../auth/AuthContext'

const navItems = [
  { to: '/dashboard', label: 'Dashboard', icon: 'dashboard' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
]

interface SidebarProps {
  isOpen: boolean
  onClose: () => void
  collapsed?: boolean
  onToggleCollapsed?: () => void
}

export default function Sidebar({ isOpen, onClose, collapsed = false, onToggleCollapsed }: SidebarProps) {
  const location = useLocation()
  const { user, logout } = useAuthContext()

  const handleLogout = async () => {
    await logout()
  }

  const isActive = (to: string) => {
    if (to === '/dashboard') return location.pathname === '/dashboard' || location.pathname.startsWith('/projects/')
    return location.pathname.startsWith(to)
  }

  // Width: full nav vs icon-only rail. Mobile drawer is always full
  // width (collapse only applies to ``md+`` breakpoints).
  const widthClass = collapsed ? 'md:w-16' : 'md:w-64'
  const padX = collapsed ? 'md:px-2' : 'md:px-4'

  return (
    <>
      {/* Backdrop - mobile only */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 md:hidden"
          onClick={onClose}
        />
      )}
      <aside
        className={`fixed left-0 top-0 w-64 ${widthClass} bg-stitch-surface-low h-screen flex flex-col py-6 px-4 ${padX} z-50 transform transition-all duration-200 ${
          isOpen ? 'translate-x-0' : '-translate-x-full'
        } md:translate-x-0 md:static md:z-auto md:shrink-0`}
      >
        {/* Logo area */}
        <div className={`mb-10 px-2 flex items-center gap-3 ${collapsed ? 'md:justify-center md:px-0' : ''}`}>
          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-stitch-primary to-stitch-primary-container flex items-center justify-center shrink-0">
            <span className="material-symbols-outlined text-stitch-on-primary-container" style={{ fontVariationSettings: "'FILL' 1" }}>
              architecture
            </span>
          </div>
          <div className={collapsed ? 'md:hidden' : ''}>
            <h1 className="text-xl font-bold tracking-[-0.04em] text-accent-text">BSNexus</h1>
            <p className="text-[10px] uppercase tracking-widest text-text-secondary font-bold">Agent Orchestrator</p>
          </div>
        </div>

        {/* Collapse toggle (desktop only) */}
        {onToggleCollapsed && (
          <button
            type="button"
            onClick={onToggleCollapsed}
            className={`hidden md:flex items-center self-end mb-3 text-text-tertiary hover:text-text-secondary transition-colors ${
              collapsed ? 'md:self-center' : ''
            }`}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              {collapsed ? 'chevron_right' : 'chevron_left'}
            </span>
          </button>
        )}

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
                title={collapsed ? item.label : undefined}
                className={`flex items-center gap-3 px-3 py-2.5 text-sm tracking-tight transition-all duration-200 rounded-lg ${
                  collapsed ? 'md:justify-center md:px-2' : ''
                } ${
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
                <span className={collapsed ? 'md:hidden' : ''}>{item.label}</span>
              </NavLink>
            )
          })}

        </nav>

        {/* Bottom section: user profile */}
        <div
          className={`mt-auto p-3 bg-stitch-surface-container rounded-xl flex items-center gap-3 ${
            collapsed ? 'md:flex-col md:p-2 md:gap-2' : ''
          }`}
        >
          <div className="w-10 h-10 rounded-full bg-stitch-surface-highest flex items-center justify-center shrink-0">
            <span className="material-symbols-outlined text-text-secondary" style={{ fontSize: '20px' }}>person</span>
          </div>
          <div className={`overflow-hidden flex-1 min-w-0 ${collapsed ? 'md:hidden' : ''}`}>
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

      </aside>
    </>
  )
}
