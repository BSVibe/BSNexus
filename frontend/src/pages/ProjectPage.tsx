import { useEffect, useMemo } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { I } from '../lib/icons'
import FilesView from '../components/files/FilesView'
import ProgressView from '../components/progress/ProgressView'
import DecisionsView from '../components/decisions/DecisionsView'
import Inspector from '../components/inside/Inspector'
import { projectsApi, type Project } from '../api/projects'
import { workspaceFilesApi } from '../api/workspaceFiles'
import { decisionsApi, deliverablesApi } from '../api/founder'

type TabId = 'progress' | 'files' | 'decisions' | 'inspector'

function parseTab(raw: string | null): TabId {
  if (raw === 'files' || raw === 'decisions' || raw === 'inspector') return raw
  return 'progress'
}

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [search, setSearch] = useSearchParams()
  const tab = parseTab(search.get('tab'))
  const focusRequestId = search.get('focusRequest')

  const { data: project } = useQuery<Project>({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId!),
    enabled: Boolean(projectId),
  })

  const { data: deliverables = [] } = useQuery({
    queryKey: ['deliverables', projectId],
    queryFn: () => deliverablesApi.listForProject(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 3000,
  })

  const { data: decisions = [] } = useQuery({
    queryKey: ['decisions', projectId],
    queryFn: () => decisionsApi.listForProject(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 3000,
  })

  const { data: files = [] } = useQuery({
    queryKey: ['workspace-files', projectId],
    queryFn: () => workspaceFilesApi.list(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 3000,
  })

  const openDecisions = useMemo(
    () => decisions.filter((d) => !d.resolved_at).length,
    [decisions],
  )

  function setTab(id: TabId) {
    const next = new URLSearchParams(search)
    if (id === 'progress') next.delete('tab')
    else next.set('tab', id)
    if (id !== 'inspector') next.delete('focusRequest')
    setSearch(next, { replace: true })
  }

  useEffect(() => {
    function onOpenInspector(e: Event) {
      const ce = e as CustomEvent<{ requestId?: string }>
      const rid = ce.detail?.requestId
      const next = new URLSearchParams(search)
      next.set('tab', 'inspector')
      if (rid) next.set('focusRequest', rid)
      else next.delete('focusRequest')
      setSearch(next, { replace: true })
    }
    document.addEventListener('bsn:open-inspector', onOpenInspector as EventListener)
    return () =>
      document.removeEventListener(
        'bsn:open-inspector',
        onOpenInspector as EventListener,
      )
  }, [search, setSearch])

  if (!projectId) {
    return (
      <div
        style={{
          padding: 48,
          textAlign: 'center',
          color: 'var(--text-tertiary)',
        }}
      >
        No project selected.
      </div>
    )
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
      }}
    >
      <div className="tabs" style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
        <TabButton
          label="Progress"
          icon={<I.Timeline size={14} />}
          active={tab === 'progress'}
          onClick={() => setTab('progress')}
          count={deliverables.length || null}
        />
        <TabButton
          label="Files"
          icon={<I.Doc size={14} />}
          active={tab === 'files'}
          onClick={() => setTab('files')}
          count={files.length || null}
        />
        <TabButton
          label="Decisions"
          icon={<I.Inbox size={14} />}
          active={tab === 'decisions'}
          onClick={() => setTab('decisions')}
          count={openDecisions || null}
          toneRose={openDecisions > 0}
        />
        <TabButton
          label="Inspector"
          icon={<I.Eye size={14} />}
          active={tab === 'inspector'}
          onClick={() => setTab('inspector')}
        />
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {tab === 'progress' && <ProgressView projectId={projectId} />}
        {tab === 'files' && (
          <FilesView projectId={projectId} projectName={project?.name ?? 'Project'} />
        )}
        {tab === 'decisions' && <DecisionsView projectId={projectId} />}
        {tab === 'inspector' && (
          <Inspector projectId={projectId} focusRequestId={focusRequestId} />
        )}
      </div>
    </div>
  )
}

function TabButton({
  label,
  icon,
  active,
  onClick,
  count,
  toneRose,
}: {
  label: string
  icon: React.ReactNode
  active: boolean
  onClick: () => void
  count?: number | null
  toneRose?: boolean
}) {
  return (
    <button
      type="button"
      className={`tab ${active ? 'active' : ''}`}
      onClick={onClick}
    >
      {icon}
      {label}
      {count != null && (
        <span className={`tab-count ${toneRose ? 'tab-count-rose' : ''}`}>
          {count}
        </span>
      )}
    </button>
  )
}
