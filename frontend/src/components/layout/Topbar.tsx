import { Fragment } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { I } from '../../lib/icons'
import { truncId } from '../../lib/fmt'
import { StatusDot } from '../common/Badge'
import { integrationsApi, type IntegrationConfigList } from '../../api/integrations'
import type { Project } from '../../api/projects'

interface TopbarProps {
  currentProject: Project | null
  onOpenInspector: () => void
  inspectorOpen: boolean
  onOpenPalette: () => void
}

interface Crumb {
  label: string
  tag?: string
  onClick?: () => void
  active?: boolean
}

export default function Topbar({
  currentProject,
  onOpenInspector,
  inspectorOpen,
  onOpenPalette,
}: TopbarProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const [search] = useSearchParams()

  const { data: integrations } = useQuery<IntegrationConfigList>({
    queryKey: ['integrations'],
    queryFn: integrationsApi.list,
  })

  const crumbs: Crumb[] = []
  if (location.pathname.startsWith('/dashboard')) {
    crumbs.push({ label: 'Dashboard', active: true })
  } else if (location.pathname.startsWith('/projects/') && currentProject) {
    crumbs.push({ label: 'Projects', onClick: () => navigate('/dashboard') })
    crumbs.push({
      label: currentProject.name,
      tag: truncId(currentProject.id),
      active: true,
    })
  } else if (location.pathname.startsWith('/settings')) {
    const section = search.get('section')
    const label =
      section === 'executors'
        ? 'Executors'
        : section === 'worker-tokens'
        ? 'Worker tokens'
        : 'Integrations'
    crumbs.push({ label: 'Settings' })
    crumbs.push({ label, active: true })
  }

  const onProject = location.pathname.startsWith('/projects/')

  return (
    <header className="tb">
      <div className="crumbs">
        {crumbs.map((c, i) => (
          <Fragment key={i}>
            {i > 0 && (
              <span className="crumb-sep">
                <I.ChevRight size={12} />
              </span>
            )}
            <span
              className={c.active ? 'crumb-active' : ''}
              onClick={c.onClick}
              style={{ cursor: c.onClick ? 'pointer' : 'default' }}
            >
              {c.label}
            </span>
            {c.tag && <span className="proj-tag">{c.tag}</span>}
          </Fragment>
        ))}
      </div>

      <div className="tb-right">
        {integrations && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              paddingRight: 12,
              borderRight: '1px solid var(--border-subtle)',
            }}
          >
            {(['bsage', 'bsgateway', 'bsupervisor'] as const).map((key) => {
              const ig = integrations[key]
              const tone = ig.enabled ? 'emerald' : 'gray'
              const label =
                key === 'bsage' ? 'BSage' : key === 'bsgateway' ? 'BSGateway' : 'BSupervisor'
              return (
                <span
                  key={key}
                  className="badge"
                  title={`${label} · ${ig.enabled ? 'enabled' : 'disabled'}`}
                  style={{ padding: '2px 6px', fontSize: 11, gap: 6 }}
                >
                  <StatusDot tone={tone} size={6} />
                  <span style={{ color: 'var(--gray-300)' }}>{label}</span>
                </span>
              )
            })}
          </div>
        )}

        {onProject && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onOpenInspector}
            title="Open Inspector (E)"
          >
            <I.Eye size={14} /> Inspector
            {inspectorOpen && (
              <span
                className="dot"
                style={{
                  background: 'var(--accent)',
                  width: 6,
                  height: 6,
                  borderRadius: 99,
                }}
              />
            )}
          </button>
        )}

        <button type="button" className="btn btn-ghost btn-sm" onClick={onOpenPalette}>
          <I.Command size={14} />
          <span style={{ color: 'var(--text-tertiary)' }}>⌘K</span>
        </button>
      </div>
    </header>
  )
}
