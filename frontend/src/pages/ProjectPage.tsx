import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import FileBrowser from '../components/workspace/FileBrowser'
import UnifiedChatSidebar from '../components/project/UnifiedChatSidebar'
import TimelineView from '../components/project/TimelineView'
import DesignView from '../components/project/DesignView'
import ProjectAgentsTab from '../components/project/ProjectAgentsTab'
import GoalSlogan from '../components/project/GoalSlogan'
import Header from '../components/layout/Header'
import PlanView from '../components/plan/PlanView'

type TabId = 'plan' | 'files' | 'timeline' | 'design' | 'agents'

const TABS: { id: TabId; label: string; icon: string }[] = [
  { id: 'plan', label: 'Plan', icon: 'account_tree' },
  { id: 'files', label: 'Files', icon: 'folder' },
  { id: 'timeline', label: 'Timeline', icon: 'timeline' },
  { id: 'design', label: 'Design', icon: 'palette' },
  { id: 'agents', label: 'Agents', icon: 'groups' },
]

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()

  if (!projectId) {
    return (
      <>
        <Header title="Project" />
        <div className="p-8">
          <div className="rounded-lg border border-dashed border-stitch-outline-variant/30 p-12 text-center">
            <p className="text-text-secondary mb-4">Select a project from the Dashboard.</p>
            <button type="button" onClick={() => navigate('/dashboard')} className="text-sm text-stitch-primary hover:underline">
              Go to Dashboard
            </button>
          </div>
        </div>
      </>
    )
  }

  return <ProjectContent projectId={projectId} />
}

function ProjectContent({ projectId }: { projectId: string }) {
  const [activeTab, setActiveTab] = useState<TabId>('plan')
  const [sidebarOpen, setSidebarOpen] = useState(true)

  // Project data
  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
    enabled: !!projectId,
  })

  return (
    <>
      {/* Header with goal slogan */}
      <Header
        title={
          <span className="flex items-center gap-2">
            {project?.name || 'Project'}
            <GoalSlogan projectId={projectId} />
          </span>
        }
        action={
          <button
            type="button"
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-2 rounded-md hover:bg-stitch-surface-container text-text-secondary transition-colors"
            title={sidebarOpen ? 'Hide chat' : 'Show chat'}
          >
            <span className="material-symbols-outlined">{sidebarOpen ? 'right_panel_close' : 'right_panel_open'}</span>
          </button>
        }
      />

      {/* Tabs */}
      <div className="px-8 flex items-center gap-1 border-b border-stitch-outline-variant/10 bg-stitch-surface">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-xs font-bold uppercase tracking-widest transition-colors border-b-2 -mb-px ${
              activeTab === tab.id
                ? 'text-stitch-primary border-stitch-primary'
                : 'text-text-tertiary border-transparent hover:text-text-secondary'
            }`}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Main content */}
      <div className="flex h-[calc(100vh-112px)] overflow-hidden">
        {/* Center */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {activeTab === 'plan' && (
            <div className="flex-1 overflow-hidden">
              <PlanView projectId={projectId} />
            </div>
          )}

          {activeTab === 'files' && (
            <div className="flex-1 overflow-hidden">
              <FileBrowser projectId={projectId} />
            </div>
          )}

          {activeTab === 'timeline' && <TimelineView />}
          {activeTab === 'design' && <DesignView />}
          {activeTab === 'agents' && <ProjectAgentsTab />}
        </div>

        {/* Right sidebar: Unified Chat */}
        {sidebarOpen && <UnifiedChatSidebar projectId={projectId} />}
      </div>
    </>
  )
}
