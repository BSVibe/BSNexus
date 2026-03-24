import { useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Bot, Settings, Sun, Moon, Monitor, LogOut } from 'lucide-react'
import { SettingsModal } from './SettingsModal'
import { useThemeStore } from '../../stores/themeStore'
import { useAuthStore } from '../../stores/authStore'

const navItems = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/architect', label: 'New Project', icon: Bot },
]

export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const { theme, setTheme } = useThemeStore()
  const signOut = useAuthStore((s) => s.signOut)
  const user = useAuthStore((s) => s.user)

  const cycleTheme = () => {
    const next = theme === 'light' ? 'dark' : theme === 'dark' ? 'system' : 'light'
    setTheme(next)
  }

  const handleLogout = async () => {
    await signOut()
    navigate('/')
  }

  const ThemeIcon = theme === 'light' ? Sun : theme === 'dark' ? Moon : Monitor
  const themeLabel = theme === 'light' ? 'Light' : theme === 'dark' ? 'Dark' : 'System'

  const isActive = (to: string) => {
    if (to === '/dashboard') return location.pathname === '/dashboard' || location.pathname.startsWith('/projects/')
    return location.pathname.startsWith(to)
  }

  return (
    <aside className="w-[200px] bg-bg-surface border-r border-border h-screen flex flex-col">
      {/* Logo area */}
      <div className="p-4 mb-4">
        <div className="flex items-center gap-3">
          <div className="bg-accent w-8 h-8 rounded-full flex items-center justify-center">
            <span className="text-white text-sm font-bold">B</span>
          </div>
          <span className="text-text-primary text-sm font-semibold">BSNexus</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex flex-col gap-1 px-3">
        {navItems.map((item) => {
          const Icon = item.icon
          const active = isActive(item.to)

          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/dashboard'}
              className={`flex items-center gap-3 px-3 py-2 text-sm font-medium transition-colors rounded-md ${
                active
                  ? 'bg-accent text-white'
                  : 'text-text-secondary hover:bg-bg-hover'
              }`}
            >
              <Icon size={18} />
              {item.label}
            </NavLink>
          )
        })}
      </nav>

      {/* Bottom section */}
      <div className="mt-auto px-3 pb-4 flex flex-col gap-1">
        {user && (
          <div className="px-3 py-2 text-xs text-text-tertiary truncate">
            {user.email}
          </div>
        )}
        <button
          type="button"
          onClick={cycleTheme}
          className="flex items-center gap-3 px-3 py-2 text-sm text-text-secondary hover:bg-bg-hover rounded-md cursor-pointer transition-colors w-full"
        >
          <ThemeIcon size={18} />
          {themeLabel}
        </button>
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          className="flex items-center gap-3 px-3 py-2 text-sm text-text-secondary hover:bg-bg-hover rounded-md cursor-pointer transition-colors w-full"
        >
          <Settings size={18} />
          Settings
        </button>
        <button
          type="button"
          onClick={handleLogout}
          className="flex items-center gap-3 px-3 py-2 text-sm text-red-500 hover:bg-red-50 rounded-md cursor-pointer transition-colors w-full"
        >
          <LogOut size={18} />
          Logout
        </button>
      </div>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </aside>
  )
}
