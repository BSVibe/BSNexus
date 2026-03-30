import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import { dashboardApi } from '../api/dashboard'
import type { ProjectDashboardSummary } from '../types/project'
import { Link, useNavigate } from 'react-router-dom'
import { Badge, Button, Modal, StatCard } from '../components/common'
import Header from '../components/layout/Header'
import { ListChecks, Bug, MessageSquare, FolderInput } from 'lucide-react'

const statusBadgeColors: Record<string, string> = {
  design: 'var(--status-queued)',
  active: 'var(--color-success)',
  paused: 'var(--color-warning)',
  completed: 'var(--status-ready)',
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: projects, isLoading, error } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  const { data: projectsSummary } = useQuery({
    queryKey: ['projects-summary'],
    queryFn: dashboardApi.getProjectsSummary,
  })

  const summaryMap = useMemo(() => {
    const map = new Map<string, ProjectDashboardSummary>()
    projectsSummary?.forEach((s) => map.set(s.id, s))
    return map
  }, [projectsSummary])

  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null)
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [showBatchDeleteModal, setShowBatchDeleteModal] = useState(false)

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelectedIds(new Set())
  }

  const deleteMutation = useMutation({
    mutationFn: (id: string) => projectsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setDeleteTarget(null)
    },
  })

  const batchDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => projectsApi.batchDelete(ids),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      exitSelectMode()
      setShowBatchDeleteModal(false)
    },
  })

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const selectAll = () => {
    if (projects) {
      setSelectedIds(new Set(projects.map((p) => p.id)))
    }
  }

  const stats = useMemo(() => {
    const list = projects || []
    const summaries = projectsSummary || []
    const totalProjects = list.length
    const completedProjects = list.filter(p => p.status === 'completed').length
    const activeProjects = list.filter(p => p.status === 'active').length
    const totalTasks = summaries.reduce((sum, s) => {
      return sum + Object.values(s.task_counts).reduce((a, b) => a + b, 0)
    }, 0)
    const doneTasks = summaries.reduce((sum, s) => sum + (s.task_counts['done'] || 0), 0)
    const totalBugs = summaries.reduce((sum, s) => sum + s.bug_count, 0)
    const completionRate = totalTasks > 0
      ? `${Math.round((doneTasks / totalTasks) * 100)}%`
      : '0%'
    return { totalProjects, completedProjects, activeProjects, totalTasks, doneTasks, totalBugs, completionRate }
  }, [projects, projectsSummary])

  if (isLoading) {
    return (
      <>
        <Header title="Dashboard" />
        <div className="p-8 flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-accent border-t-transparent" />
        </div>
      </>
    )
  }

  if (error) {
    return (
      <>
        <Header title="Dashboard" />
        <div className="p-8">
          <div className="rounded-xl border border-error/30 bg-error/5 p-6 text-sm text-error text-center">
            Failed to load projects. Please try again.
          </div>
        </div>
      </>
    )
  }

  return (
    <>
      <Header title="Dashboard" action={
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={() => navigate('/migrate')}>
            <FolderInput size={14} className="mr-1.5" />
            Import
          </Button>
          <Button size="sm" onClick={() => navigate('/architect', { state: { openNewSession: true } })}>New Project</Button>
        </div>
      } />
      <div className="p-8 max-w-7xl mx-auto">
        {/* Stat Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <StatCard
            label="Projects"
            value={stats.totalProjects}
            subtext={`${stats.activeProjects} active, ${stats.completedProjects} completed`}
          />
          <StatCard
            label="Tasks"
            value={stats.totalTasks}
            subtext={`${stats.doneTasks} done`}
          />
          <StatCard
            label="Bugs"
            value={stats.totalBugs}
            subtext="auto-detected"
          />
          <StatCard
            label="Completion"
            value={stats.completionRate}
            subtext={`${stats.doneTasks} of ${stats.totalTasks} tasks`}
          />
        </div>

        {/* Project List Header with Batch Actions */}
        {(projects?.length ?? 0) > 0 && (
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-bold text-text-primary tracking-tight">
                Projects <span className="text-text-muted font-normal text-sm ml-1">({projects?.length})</span>
              </h2>
              {!selectMode && (
                <button
                  onClick={() => setSelectMode(true)}
                  className="p-1.5 rounded-md text-text-tertiary hover:text-text-primary hover:bg-bg-hover transition-colors"
                  title="Select mode"
                >
                  <ListChecks size={16} />
                </button>
              )}
            </div>
            {selectMode && (
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="sm" onClick={selectAll}>All</Button>
                {selectedIds.size > 0 && (
                  <>
                    <span className="text-xs text-text-secondary bg-bg-elevated px-2.5 py-1 rounded-full border border-border/50">
                      {selectedIds.size} selected
                    </span>
                    <Button
                      size="sm"
                      className="!bg-error hover:!bg-error/80"
                      onClick={() => setShowBatchDeleteModal(true)}
                    >
                      Delete
                    </Button>
                  </>
                )}
                <Button variant="secondary" size="sm" onClick={exitSelectMode}>Cancel</Button>
              </div>
            )}
          </div>
        )}

        {/* Project List */}
        {projects?.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border p-16 text-center">
            <div className="bg-accent/10 w-16 h-16 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <span className="text-accent text-2xl font-bold">+</span>
            </div>
            <p className="text-text-secondary mb-2 font-medium">No projects yet</p>
            <p className="text-sm text-text-muted mb-6">Start by creating one with the Architect.</p>
            <Button onClick={() => navigate('/architect', { state: { openNewSession: true } })}>
              Start with Architect
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {projects?.map((project) => {
              const badgeColor = statusBadgeColors[project.status] || statusBadgeColors.design
              const phaseCount = project.phases.length
              const isSelected = selectedIds.has(project.id)

              return (
                <div
                  key={project.id}
                  onClick={selectMode ? () => toggleSelect(project.id) : undefined}
                  className={`relative rounded-xl border bg-bg-card p-5 transition-all group ${
                    selectMode ? 'cursor-pointer' : ''
                  } ${
                    isSelected ? 'border-accent ring-1 ring-accent/30' : 'border-border/40 hover:border-accent/30 hover:shadow-lg hover:shadow-black/20'
                  }`}
                >
                  {/* Checkbox (select mode only) */}
                  {selectMode && (
                    <div
                      className={`absolute top-3 left-3 w-5 h-5 rounded border flex items-center justify-center transition-colors ${
                        isSelected
                          ? 'border-accent bg-accent'
                          : 'border-border-subtle'
                      }`}
                    >
                      {isSelected && (
                        <svg className="w-3.5 h-3.5 text-gray-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                    </div>
                  )}
                  {/* Delete button */}
                  {!selectMode && (
                    <button
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        setDeleteTarget({ id: project.id, name: project.name })
                      }}
                      className="absolute top-3 right-3 p-1.5 rounded-lg text-text-tertiary hover:text-error hover:bg-error/10 opacity-0 group-hover:opacity-100 transition-all"
                      title="Delete project"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                  {selectMode ? (
                    <div className="pl-4">
                      <div className="flex items-start justify-between mb-3">
                        <h3 className="text-base font-semibold text-text-primary">{project.name}</h3>
                        <Badge color={badgeColor} label={project.status} />
                      </div>
                      <p className="text-sm text-text-secondary mt-1 mb-4 line-clamp-2">{project.description}</p>
                      <div className="flex items-center justify-between text-xs text-text-tertiary">
                        <span>{phaseCount} phase{phaseCount !== 1 ? 's' : ''}</span>
                        <span>{new Date(project.updated_at).toLocaleDateString()}</span>
                      </div>
                    </div>
                  ) : (
                    <Link
                      to={`/projects/${project.id}`}
                      className="block cursor-pointer"
                    >
                      <div className="flex items-start justify-between mb-3 pr-6">
                        <h3 className="text-base font-semibold text-text-primary">{project.name}</h3>
                        <Badge color={badgeColor} label={project.status} />
                      </div>
                      <p className="text-sm text-text-secondary mt-1 mb-4 line-clamp-2 leading-relaxed">{project.description}</p>

                      {/* Task distribution bar */}
                      {(() => {
                        const summary = summaryMap.get(project.id)
                        if (!summary) return null
                        const counts = summary.task_counts
                        const total = Object.values(counts).reduce((a, b) => a + b, 0)
                        if (total === 0) return null
                        const done = counts['done'] || 0
                        const inProgress = (counts['in_progress'] || 0) + (counts['review'] || 0)
                        const pctDone = Math.round((done / total) * 100)
                        const pctInProgress = Math.round((inProgress / total) * 100)
                        return (
                          <div className="mb-3">
                            <div className="flex items-center gap-2 mb-1.5">
                              <div className="flex-1 h-1.5 rounded-full bg-bg-hover overflow-hidden flex">
                                <div className="h-full bg-success rounded-l-full" style={{ width: `${pctDone}%` }} />
                                <div className="h-full bg-accent" style={{ width: `${pctInProgress}%` }} />
                              </div>
                              <span className="text-xs font-medium text-text-secondary">{pctDone}%</span>
                            </div>
                            <div className="flex items-center gap-3 text-xs text-text-muted">
                              <span>{total} tasks</span>
                              {summary.bug_count > 0 && (
                                <span className="flex items-center gap-0.5 text-error">
                                  <Bug size={11} />
                                  {summary.bug_count}
                                </span>
                              )}
                              {summary.has_architect_session && (
                                <span className="flex items-center gap-0.5 text-accent">
                                  <MessageSquare size={11} />
                                  Architect
                                </span>
                              )}
                            </div>
                          </div>
                        )
                      })()}

                      <div className="flex items-center justify-between text-xs text-text-muted pt-2 border-t border-border/50">
                        <span>{phaseCount} phase{phaseCount !== 1 ? 's' : ''}</span>
                        <span>{new Date(project.updated_at).toLocaleDateString()}</span>
                      </div>
                    </Link>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Delete Confirmation Modal */}
      <Modal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Delete Project"
        width={420}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
              className="!bg-error hover:!bg-error/80"
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-text-secondary text-sm">
          Are you sure you want to delete <strong className="text-text-primary">{deleteTarget?.name}</strong>?
          This will permanently remove the project and all its phases, tasks, and history.
        </p>
      </Modal>

      {/* Batch Delete Confirmation Modal */}
      <Modal
        open={showBatchDeleteModal}
        onClose={() => setShowBatchDeleteModal(false)}
        title="Delete Projects"
        width={420}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setShowBatchDeleteModal(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              loading={batchDeleteMutation.isPending}
              onClick={() => batchDeleteMutation.mutate([...selectedIds])}
              className="!bg-error hover:!bg-error/80"
            >
              Delete {selectedIds.size} Projects
            </Button>
          </>
        }
      >
        <p className="text-text-secondary text-sm">
          Are you sure you want to delete <strong className="text-text-primary">{selectedIds.size} projects</strong>?
          This will permanently remove all selected projects and their phases, tasks, and history.
        </p>
      </Modal>
    </>
  )
}
