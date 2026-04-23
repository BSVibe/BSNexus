import { useLocation, useNavigate } from 'react-router-dom'

import { StatusDot } from '../common/Badge'
import { I } from '../../lib/icons'
import { statusTone } from '../../lib/tone'
import { useAuthContext } from '../auth/AuthContext'
import type { Project } from '../../api/projects'

interface SidebarProps {
  projects: Project[]
  onOpenPalette: () => void
}

export default function Sidebar({ projects, onOpenPalette }: SidebarProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuthContext()

  const activeProjectId = location.pathname.startsWith('/projects/')
    ? location.pathname.split('/')[2]
    : null
  const onDashboard = location.pathname === '/dashboard'
  const onSettings = location.pathname.startsWith('/settings')

  const initials = (user?.email ?? '??').slice(0, 2).toUpperCase()
  const displayName = (user?.email ?? 'guest').split('@')[0]

  return (
    <aside className="sb">
      <button
        type="button"
        className="sb-brand"
        onClick={() => navigate('/dashboard')}
        aria-label="Home"
      >
        <div className="sb-logo">BN</div>
        <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1 }}>
          <span className="sb-brand-name">BSNexus</span>
        </div>
      </button>

      <button
        type="button"
        className="chip"
        style={{
          margin: '12px 12px 4px',
          padding: '8px 10px',
          justifyContent: 'space-between',
        }}
        onClick={onOpenPalette}
        title="Jump anywhere"
      >
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            color: 'var(--text-tertiary)',
          }}
        >
          <I.Search size={14} /> Jump to…
        </span>
        <span style={{ display: 'inline-flex', gap: 4 }}>
          <kbd>⌘</kbd>
          <kbd>K</kbd>
        </span>
      </button>

      <div className="sb-section">
        <span>Projects</span>
        <span className="sb-count">{projects.length}</span>
      </div>
      <div className="sb-list">
        {projects.map((p) => {
          const tone = statusTone(p.status)
          const isActive = activeProjectId === p.id
          return (
            <button
              key={p.id}
              type="button"
              className={`sb-item ${isActive ? 'active' : ''}`}
              onClick={() => navigate(`/projects/${p.id}`)}
              title={p.name}
            >
              <StatusDot tone={tone} size={6} />
              <span className="label">{p.name}</span>
            </button>
          )
        })}
        {projects.length === 0 && (
          <span
            style={{
              padding: '6px 8px',
              fontSize: 11,
              color: 'var(--text-tertiary)',
            }}
          >
            No projects yet.
          </span>
        )}
        <button
          type="button"
          className="sb-item"
          style={{ color: 'var(--text-tertiary)', marginTop: 8 }}
          onClick={() => navigate('/dashboard')}
        >
          <I.Plus size={14} /> <span className="label">New project</span>
        </button>

        <div style={{ flex: 1 }} />

        <div className="sb-section" style={{ paddingTop: 24 }}>
          Workspace
        </div>
        <button
          type="button"
          className={`sb-item ${onDashboard ? 'active' : ''}`}
          onClick={() => navigate('/dashboard')}
        >
          <I.Home size={14} /> <span className="label">Dashboard</span>
        </button>
        <button
          type="button"
          className={`sb-item ${onSettings ? 'active' : ''}`}
          onClick={() => navigate('/settings')}
        >
          <I.Settings size={14} /> <span className="label">Settings</span>
        </button>
      </div>

      <div className="sb-footer">
        <div className="sb-avatar">{initials}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            className="sb-user-name"
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {displayName}
          </div>
          <div
            className="sb-user-mail"
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {user?.email}
          </div>
        </div>
        <button
          type="button"
          className="btn btn-icon"
          title="Log out"
          onClick={() => {
            void logout()
          }}
        >
          <I.Logout size={14} />
        </button>
      </div>
    </aside>
  )
}
