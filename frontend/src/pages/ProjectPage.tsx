import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { I } from '../lib/icons'
import FilesView from '../components/files/FilesView'
import ProgressView from '../components/progress/ProgressView'
import DecisionsView from '../components/decisions/DecisionsView'
import { SAMPLE_FILES } from '../lib/bsd-sample'
import { projectsApi, type Project } from '../api/projects'
import { decisionsApi, deliverablesApi } from '../api/founder'

type TabId = 'progress' | 'files' | 'decisions'

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [tab, setTab] = useState<TabId>('progress')

  const { data: project } = useQuery<Project>({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId!),
    enabled: Boolean(projectId),
  })

  const { data: deliverables = [] } = useQuery({
    queryKey: ['deliverables', projectId],
    queryFn: () => deliverablesApi.listForProject(projectId!),
    enabled: Boolean(projectId),
  })

  const { data: decisions = [] } = useQuery({
    queryKey: ['decisions', projectId],
    queryFn: () => decisionsApi.listForProject(projectId!),
    enabled: Boolean(projectId),
  })

  const openDecisions = useMemo(
    () => decisions.filter((d) => !d.resolved_at).length,
    [decisions],
  )

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
        <div
          className="tab"
          style={{
            pointerEvents: 'none',
            padding: '10px 0',
            marginRight: 16,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            borderBottom: 'none',
          }}
        >
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--gray-50)',
              lineHeight: '16px',
            }}
          >
            {project?.name ?? 'Project'}
          </div>
          <div
            className="mono faded"
            style={{ fontSize: 10, marginTop: 2 }}
            title={projectId}
          >
            {projectId.slice(0, 8)}
          </div>
        </div>
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
          count={SAMPLE_FILES.length}
        />
        <TabButton
          label="Decisions"
          icon={<I.Inbox size={14} />}
          active={tab === 'decisions'}
          onClick={() => setTab('decisions')}
          count={openDecisions || null}
          toneRose={openDecisions > 0}
        />
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {tab === 'progress' && <ProgressView projectId={projectId} />}
        {tab === 'files' && <FilesView projectName={project?.name ?? 'Project'} />}
        {tab === 'decisions' && <DecisionsView projectId={projectId} />}
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
