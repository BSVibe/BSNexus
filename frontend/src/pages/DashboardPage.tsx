import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import { dashboardApi } from '../api/dashboard'
import { budgetApi } from '../api/budget'
import type { ProjectDashboardSummary } from '../types/project'
import { Link } from 'react-router-dom'
import { Badge, Button, Modal, StatCard } from '../components/common'
import Header from '../components/layout/Header'

const statusBadgeColors: Record<string, string> = {
  design: 'var(--status-queued)',
  active: 'var(--color-success)',
  paused: 'var(--color-warning)',
  completed: 'var(--status-ready)',
}

const INPUT_CLASS =
  'w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary'

export default function DashboardPage() {
  const queryClient = useQueryClient()
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newWorkspaceType, setNewWorkspaceType] = useState<'server_managed' | 'local_import'>('server_managed')
  const [newRepoPath, setNewRepoPath] = useState('')

  const createMutation = useMutation({
    mutationFn: () => projectsApi.create({
      name: newName,
      description: newDesc,
      workspace_type: newWorkspaceType,
      repo_path: newWorkspaceType === 'local_import' && newRepoPath ? newRepoPath : undefined,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['projects-summary'] })
      setCreateModalOpen(false)
      setNewName('')
      setNewDesc('')
      setNewWorkspaceType('server_managed')
      setNewRepoPath('')
    },
  })

  const { data: projects, isLoading, error } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  const { data: projectsSummary } = useQuery({
    queryKey: ['projects-summary'],
    queryFn: dashboardApi.getProjectsSummary,
  })

  const { data: budgetOverview } = useQuery({
    queryKey: ['budget', 'summary'],
    queryFn: budgetApi.getSummary,
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

  const invalidateAfterDelete = (ids: string[]) => {
    queryClient.invalidateQueries({ queryKey: ['projects'] })
    queryClient.invalidateQueries({ queryKey: ['projects-summary'] })
    // Evict per-project caches so stale data doesn't linger.
    for (const id of ids) {
      queryClient.removeQueries({ queryKey: ['project', id] })
      queryClient.removeQueries({ queryKey: ['project-chat', id] })
      queryClient.removeQueries({ queryKey: ['plan-tree', id] })
    }
  }

  const deleteMutation = useMutation({
    mutationFn: (id: string) => projectsApi.delete(id),
    onSuccess: (_data, id) => {
      invalidateAfterDelete([id])
      setDeleteTarget(null)
    },
  })

  const batchDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => projectsApi.batchDelete(ids),
    onSuccess: (_data, ids) => {
      invalidateAfterDelete(ids)
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
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-stitch-primary border-t-transparent" />
        </div>
      </>
    )
  }

  if (error) {
    return (
      <>
        <Header title="Dashboard" />
        <div className="p-8">
          <div className="rounded-xl border border-stitch-error/30 bg-stitch-error-container/10 p-6 text-sm text-stitch-error text-center">
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
          <button
            onClick={() => setCreateModalOpen(true)}
            className="bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container px-4 py-1.5 rounded-md text-sm font-bold shadow-lg shadow-stitch-primary/20 hover:opacity-90 transition-opacity"
          >
            New Project
          </button>
        </div>
      } />
      <div className="flex-1 overflow-auto p-8 max-w-[1600px] mx-auto w-full">
        {/* Stat Cards (Bento-style) */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-10">
          <StatCard label="Total Projects" value={stats.totalProjects} icon="folder_open" />
          <StatCard label="Active Tasks" value={stats.totalTasks} subtext={`${stats.doneTasks} done`} />
          <StatCard label="Completion Rate" value={stats.completionRate} icon="bolt" />
          <StatCard label="Compute Cost" value={budgetOverview ? `$${(budgetOverview.total_spent_cents / 100).toFixed(2)}` : '$0.00'} icon="payments" subtext={budgetOverview?.total_budget_cents ? `of $${(budgetOverview.total_budget_cents / 100).toFixed(2)}` : undefined} />
        </div>

        {/* Project List Header with Batch Actions */}
        {(projects?.length ?? 0) > 0 && (
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <h2 className="text-xs font-bold uppercase tracking-[0.05em] text-text-secondary">
                Projects <span className="ml-2 text-[10px] opacity-50">{projects?.length}</span>
              </h2>
              {!selectMode && (
                <button
                  onClick={() => setSelectMode(true)}
                  className="p-1.5 rounded-md text-text-tertiary hover:text-text-primary hover:bg-stitch-surface-container transition-colors"
                  title="Select mode"
                >
                  <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>checklist</span>
                </button>
              )}
            </div>
            {selectMode && (
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="sm" onClick={selectAll}>All</Button>
                {selectedIds.size > 0 && (
                  <>
                    <span className="text-xs text-text-secondary bg-stitch-surface-container px-2.5 py-1 rounded-full border border-stitch-outline-variant/20">
                      {selectedIds.size} selected
                    </span>
                    <Button
                      size="sm"
                      className="!bg-stitch-error-container hover:!bg-stitch-error-container/80 !text-stitch-error"
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
          <div className="rounded-xl border border-dashed border-stitch-outline-variant/30 p-16 text-center">
            <div className="w-16 h-16 rounded-2xl bg-stitch-primary/10 flex items-center justify-center mx-auto mb-4">
              <span className="material-symbols-outlined text-stitch-primary text-3xl">add</span>
            </div>
            <p className="text-text-secondary mb-2 font-medium">No projects yet</p>
            <p className="text-sm text-text-tertiary mb-6">Create your first project to get started.</p>
            <button
              onClick={() => setCreateModalOpen(true)}
              className="bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container px-6 py-2.5 rounded-md text-sm font-bold shadow-lg shadow-stitch-primary/20"
            >
              Create Project
            </button>
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
                  className={`relative bg-stitch-surface-container p-5 rounded-lg transition-all group border ${
                    selectMode ? 'cursor-pointer' : ''
                  } ${
                    isSelected
                      ? 'border-stitch-primary ring-1 ring-stitch-primary/30'
                      : 'border-stitch-outline-variant/10 hover:border-stitch-primary/20 hover:bg-stitch-surface-high'
                  }`}
                >
                  {/* Checkbox (select mode only) */}
                  {selectMode && (
                    <div
                      className={`absolute top-3 left-3 w-5 h-5 rounded border flex items-center justify-center transition-colors ${
                        isSelected
                          ? 'border-stitch-primary bg-stitch-primary'
                          : 'border-stitch-outline-variant'
                      }`}
                    >
                      {isSelected && (
                        <span className="material-symbols-outlined text-stitch-on-primary" style={{ fontSize: '14px' }}>check</span>
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
                      className="absolute top-3 right-3 p-1.5 rounded-lg text-text-tertiary hover:text-stitch-error hover:bg-stitch-error-container/10 opacity-0 group-hover:opacity-100 transition-all"
                      title="Delete project"
                    >
                      <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>close</span>
                    </button>
                  )}
                  {selectMode ? (
                    <div className="pl-4">
                      <div className="flex items-start justify-between mb-3">
                        <h3 className="text-sm font-semibold text-white">{project.name}</h3>
                        <Badge color={badgeColor} label={project.status} />
                      </div>
                      <p className="text-xs text-text-secondary mt-1 mb-4 line-clamp-2">{project.description}</p>
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
                        <h4 className="text-sm font-semibold text-white leading-snug">{project.name}</h4>
                        <span className="px-2 py-0.5 rounded-full bg-stitch-secondary-container text-stitch-on-secondary-container text-[10px] font-bold">
                          {project.status}
                        </span>
                      </div>
                      <p className="text-xs text-text-secondary mt-1 mb-4 line-clamp-2 leading-relaxed">{project.description}</p>

                      {/* Task distribution bar */}
                      {(() => {
                        const summary = summaryMap.get(project.id)
                        if (!summary) return null
                        const counts = summary.task_counts
                        const total = Object.values(counts).reduce((a, b) => a + b, 0)
                        if (total === 0) return null
                        const done = counts['done'] || 0
                        const inProgress = counts['running'] || 0
                        const pctDone = Math.round((done / total) * 100)
                        const pctInProgress = Math.round((inProgress / total) * 100)
                        return (
                          <div className="mb-3">
                            <div className="flex items-center gap-2 mb-1.5">
                              <div className="flex-1 h-1.5 rounded-full bg-stitch-surface-lowest overflow-hidden flex">
                                <div className="h-full bg-stitch-primary rounded-l-full" style={{ width: `${pctDone}%` }} />
                                <div className="h-full bg-stitch-secondary" style={{ width: `${pctInProgress}%` }} />
                              </div>
                              <span className="text-xs font-medium text-text-secondary">{pctDone}%</span>
                            </div>
                            <div className="flex items-center gap-3 text-xs text-text-tertiary">
                              <span>{total} tasks</span>
                              {summary.bug_count > 0 && (
                                <span className="flex items-center gap-0.5 text-stitch-error">
                                  <span className="material-symbols-outlined" style={{ fontSize: '12px' }}>bug_report</span>
                                  {summary.bug_count}
                                </span>
                              )}
                            </div>
                          </div>
                        )
                      })()}

                      <div className="flex items-center justify-between text-xs text-text-tertiary pt-2 border-t border-stitch-outline-variant/10">
                        <span>{phaseCount} phase{phaseCount !== 1 ? 's' : ''}</span>
                        <span className="flex items-center gap-1">
                          <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>event</span>
                          {new Date(project.updated_at).toLocaleDateString()}
                        </span>
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
              className="!bg-stitch-error-container hover:!bg-stitch-error-container/80 !text-stitch-error"
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-text-secondary text-sm">
          Are you sure you want to delete <strong className="text-white">{deleteTarget?.name}</strong>?
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
              className="!bg-stitch-error-container hover:!bg-stitch-error-container/80 !text-stitch-error"
            >
              Delete {selectedIds.size} Projects
            </Button>
          </>
        }
      >
        <p className="text-text-secondary text-sm">
          Are you sure you want to delete <strong className="text-white">{selectedIds.size} projects</strong>?
          This will permanently remove all selected projects and their phases, tasks, and history.
        </p>
      </Modal>

      {/* Create Project Modal */}
      <Modal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        title="New Project"
        width={480}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setCreateModalOpen(false)}>Cancel</Button>
            <Button
              variant="primary"
              size="sm"
              loading={createMutation.isPending}
              onClick={() => createMutation.mutate()}
              disabled={!newName.trim() || (newWorkspaceType === 'local_import' && !newRepoPath.trim())}
            >
              Create
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-sm text-text-secondary mb-1.5">Project Name *</label>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. BSNexus Mobile App"
              className={INPUT_CLASS}
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm text-text-secondary mb-1.5">Description</label>
            <textarea
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="Brief description of the project"
              rows={3}
              className={INPUT_CLASS + ' resize-none'}
            />
          </div>
          <div>
            <label className="block text-sm text-text-secondary mb-2">Workspace</label>
            <div className="grid grid-cols-2 gap-3">
              {([
                { value: 'server_managed' as const, icon: 'cloud', label: 'Server', desc: 'Hosted workspace. Browse files in the web UI.' },
                { value: 'local_import' as const, icon: 'computer', label: 'Local', desc: 'Self-hosted worker required. Agent works on your machine.' },
              ]).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setNewWorkspaceType(opt.value)}
                  className={`p-3 rounded-lg border text-left transition-all ${
                    newWorkspaceType === opt.value
                      ? 'border-stitch-primary bg-stitch-primary/5'
                      : 'border-stitch-outline-variant/20 hover:border-stitch-outline-variant/40'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className="material-symbols-outlined" style={{ fontSize: '16px', color: newWorkspaceType === opt.value ? 'var(--stitch-primary)' : undefined }}>
                      {opt.icon}
                    </span>
                    <span className={`text-sm font-bold ${newWorkspaceType === opt.value ? 'text-stitch-primary' : 'text-text-primary'}`}>
                      {opt.label}
                    </span>
                  </div>
                  <p className="text-[10px] text-text-tertiary leading-relaxed">{opt.desc}</p>
                </button>
              ))}
            </div>
            {newWorkspaceType === 'local_import' && (
              <div className="mt-3">
                <label className="block text-xs text-text-tertiary mb-1">Project Path *</label>
                <input
                  type="text"
                  value={newRepoPath}
                  onChange={(e) => setNewRepoPath(e.target.value)}
                  placeholder="/home/user/projects/my-app"
                  className="w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary"
                />
              </div>
            )}
          </div>
        </div>
      </Modal>
    </>
  )
}
