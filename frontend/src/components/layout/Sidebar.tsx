'use client'

import { useTranslations } from 'next-intl'
import { usePathname, useRouter } from 'next/navigation'

import { StatusDot } from '../common/Badge'
import { I } from '../../lib/icons'
import { statusTone } from '../../lib/tone'
import { useAuthContext } from '../auth/AuthContext'
import type { Project } from '../../api/projects'

interface SidebarProps {
  projects: Project[]
  onOpenPalette: () => void
  onNavigate?: () => void
}

export default function Sidebar({ projects, onOpenPalette, onNavigate }: SidebarProps) {
  const router = useRouter()
  const pathname = usePathname() ?? ''
  const { user, logout } = useAuthContext()
  const t = useTranslations('nexus.layout')
  const tAuth = useTranslations('nexus.auth')

  const activeProjectId = pathname.startsWith('/projects/')
    ? pathname.split('/')[2]
    : null
  const onDashboard = pathname === '/dashboard'
  const onSettings = pathname.startsWith('/settings')

  const navigate = (href: string) => {
    onNavigate?.()
    router.push(href)
  }

  const initials = (user?.email ?? '??').slice(0, 2).toUpperCase()
  const displayName = (user?.email ?? tAuth('guest')).split('@')[0]

  return (
    <aside className="sb">
      <button
        type="button"
        className="sb-brand"
        onClick={() => navigate('/dashboard')}
        aria-label={tAuth('home')}
      >
        <div className="sb-logo">BN</div>
        <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1 }}>
          <span className="sb-brand-name">{t('brandName')}</span>
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
        title={t('jumpAnywhere')}
      >
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            color: 'var(--text-tertiary)',
          }}
        >
          <I.Search size={14} /> {t('jumpTo')}
        </span>
        <span style={{ display: 'inline-flex', gap: 4 }}>
          <kbd>⌘</kbd>
          <kbd>K</kbd>
        </span>
      </button>

      <div className="sb-section">
        <span>{t('projects')}</span>
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
            {t('noProjects')}
          </span>
        )}
        <button
          type="button"
          className="sb-item"
          style={{ color: 'var(--text-tertiary)', marginTop: 8 }}
          onClick={() => navigate('/dashboard')}
        >
          <I.Plus size={14} /> <span className="label">{t('newProject')}</span>
        </button>

        <div style={{ flex: 1 }} />

        <div className="sb-section" style={{ paddingTop: 24 }}>
          {t('workspace')}
        </div>
        <button
          type="button"
          className={`sb-item ${onDashboard ? 'active' : ''}`}
          onClick={() => navigate('/dashboard')}
        >
          <I.Home size={14} /> <span className="label">{t('dashboard')}</span>
        </button>
        <button
          type="button"
          className={`sb-item ${onSettings ? 'active' : ''}`}
          onClick={() => navigate('/settings')}
        >
          <I.Settings size={14} /> <span className="label">{t('settings')}</span>
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
          title={tAuth('logout')}
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
