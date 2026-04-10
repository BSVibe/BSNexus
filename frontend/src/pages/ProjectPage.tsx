import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import FileBrowser from '../components/workspace/FileBrowser'
import UnifiedChatSidebar from '../components/project/UnifiedChatSidebar'
import DesignView from '../components/project/DesignView'
import ProjectAgentsTab from '../components/project/ProjectAgentsTab'
import ProjectChannelsModal from '../components/project/ProjectChannelsModal'
import GoalSlogan from '../components/project/GoalSlogan'
import Header from '../components/layout/Header'
import PlanView from '../components/plan/PlanView'

// Timeline tab was a placeholder ("Gantt chart coming soon") and has been
// removed. The Plan tab covers task progress; the Agents tab stays as a
// detail view for the per-agent status / drill-down (the Plan view's
// AgentStatusBar is the at-a-glance summary).
type TabId = 'plan' | 'files' | 'design' | 'agents'

const TABS: { id: TabId; label: string; icon: string }[] = [
  { id: 'plan', label: 'Plan', icon: 'account_tree' },
  { id: 'files', label: 'Files', icon: 'folder' },
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
  const [channelsOpen, setChannelsOpen] = useState(false)

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
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setChannelsOpen(true)}
              className="p-2 rounded-md hover:bg-stitch-surface-container text-text-secondary transition-colors"
              title="Channels"
            >
              <span className="material-symbols-outlined">forum</span>
            </button>
            <button
              type="button"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="p-2 rounded-md hover:bg-stitch-surface-container text-text-secondary transition-colors"
              title={sidebarOpen ? 'Hide chat' : 'Show chat'}
            >
              <span className="material-symbols-outlined">{sidebarOpen ? 'right_panel_close' : 'right_panel_open'}</span>
            </button>
          </div>
        }
      />
      <ProjectChannelsModal
        open={channelsOpen}
        projectId={projectId}
        onClose={() => setChannelsOpen(false)}
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

          {activeTab === 'design' && <DesignView projectId={projectId} />}
          {activeTab === 'agents' && <ProjectAgentsTab />}
        </div>

        {/* Right sidebar: Unified Chat */}
        {sidebarOpen && <UnifiedChatSidebar projectId={projectId} />}
      </div>
    </>
  )
}
