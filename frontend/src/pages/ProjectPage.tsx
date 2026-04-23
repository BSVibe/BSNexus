import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import Header from '../components/layout/Header'
import DirectionView from '../components/direction/DirectionView'
import ProgressView from '../components/progress/ProgressView'
import DecisionsView from '../components/decisions/DecisionsView'
import InsideView from '../components/inside/InsideView'
import { projectsApi, type Project } from '../api/projects'

type TabId = 'direction' | 'progress' | 'decisions' | 'inside'

const TABS: Array<{ id: TabId; label: string; optIn?: boolean }> = [
  { id: 'direction', label: 'Direction' },
  { id: 'progress', label: 'Progress' },
  { id: 'decisions', label: 'Decisions' },
  { id: 'inside', label: 'Inside', optIn: true },
]

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [tab, setTab] = useState<TabId>('direction')
  const [insideEnabled, setInsideEnabled] = useState(false)

  const { data: project } = useQuery<Project>({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId!),
    enabled: Boolean(projectId),
  })

  const visibleTabs = TABS.filter((t) => !t.optIn || insideEnabled)

  return (
    <>
      <Header
        title={project?.name ?? 'Project'}
        action={
          <label className="flex items-center gap-2 text-xs text-text-tertiary">
            <input
              type="checkbox"
              checked={insideEnabled}
              onChange={(e) => {
                setInsideEnabled(e.target.checked)
                if (!e.target.checked && tab === 'inside') setTab('direction')
              }}
            />
            Show Inside panel
          </label>
        }
      />

      <nav className="flex gap-1 border-b border-border px-4">
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm transition-colors ${
              tab === t.id
                ? 'border-b-2 border-accent text-text-primary'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="h-[calc(100vh-8rem)]">
        {tab === 'direction' && <DirectionView projectId={projectId} />}
        {tab === 'progress' && <ProgressView projectId={projectId} />}
        {tab === 'decisions' && <DecisionsView projectId={projectId} />}
        {tab === 'inside' && insideEnabled && <InsideView projectId={projectId} />}
      </div>
    </>
  )
}
