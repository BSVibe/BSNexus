import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  planTreeApi,
  type ActivityEntry,
  type PlanPhaseNode,
  type PlanTaskNode,
  type PlanTreeResponse,
  type TaskStatus,
} from '../../api/planTree'
import { usePlanStore } from '../../stores/planStore'

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  blocked: 'Blocked',
  done: 'Done',
}

const STATUS_BADGE: Record<TaskStatus, string> = {
  pending: 'bg-stitch-outline-variant/15 text-text-tertiary',
  running: 'bg-emerald-500/15 text-emerald-400',
  blocked: 'bg-rose-500/15 text-rose-400',
  done: 'bg-stitch-primary/15 text-stitch-primary',
}

interface DetailPanelProps {
  projectId: string
}

export default function DetailPanel({ projectId }: DetailPanelProps) {
  const selectedNode = usePlanStore((s) => s.selectedNode)
  const treeQuery = useQuery<PlanTreeResponse>({
    queryKey: ['plan-tree', projectId],
    queryFn: () => planTreeApi.getTree(projectId),
    enabled: !!projectId,
  })

  if (!selectedNode) {
    return (
      <div className="flex h-full items-center justify-center text-text-tertiary text-sm">
        Select a phase or task in the tree to see details
      </div>
    )
  }

  if (!treeQuery.data) {
    return null
  }

  if (selectedNode.type === 'phase') {
    const phase = treeQuery.data.phases.find((p) => p.id === selectedNode.id)
    if (!phase) {
      return <div className="p-6 text-text-tertiary text-sm">Phase no longer exists</div>
    }
    return <PhaseDetail phase={phase} />
  }

  // task selection
  for (const phase of treeQuery.data.phases) {
    const task = phase.tasks.find((t) => t.id === selectedNode.id)
    if (task) {
      return <TaskDetail task={task} phase={phase} tree={treeQuery.data} />
    }
  }
  return <div className="p-6 text-text-tertiary text-sm">Task no longer exists</div>
}


// ── Phase detail ────────────────────────────────────────────────────


function PhaseDetail({ phase }: { phase: PlanPhaseNode }) {
  const total = phase.tasks.length
  const done = phase.tasks.filter((t) => t.status === 'done').length
  const pct = total === 0 ? 0 : Math.round((done / total) * 100)

  return (
    <div className="flex h-full flex-col overflow-y-auto p-6">
      <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-text-tertiary">
        Phase
      </div>
      <h2 className="mb-2 text-2xl font-bold text-text-primary">{phase.name}</h2>
      {phase.description && (
        <p className="mb-4 text-sm text-text-secondary leading-relaxed">{phase.description}</p>
      )}

      <div className="mb-6 flex items-center gap-3">
        <div className="flex-1 h-1.5 rounded-full bg-stitch-surface-low overflow-hidden">
          <div
            className="h-full rounded-full bg-stitch-primary transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-xs text-text-tertiary tabular-nums">
          {done}/{total} ({pct}%)
        </span>
      </div>

      <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-text-tertiary">Tasks</h3>
      <div className="flex flex-col gap-1">
        {phase.tasks.map((task) => (
          <div
            key={task.id}
            className="flex items-center gap-2 rounded border border-stitch-outline-variant/10 bg-stitch-surface-low px-3 py-2"
          >
            <span className="text-xs text-text-secondary truncate flex-1">{task.title}</span>
            <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${STATUS_BADGE[task.status]}`}>
              {task.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}


// ── Task detail ─────────────────────────────────────────────────────


interface TaskDetailProps {
  task: PlanTaskNode
  phase: PlanPhaseNode
  tree: PlanTreeResponse
}

function TaskDetail({ task, phase, tree }: TaskDetailProps) {
  const [showToolLog, setShowToolLog] = useState(false)
  const activityQuery = useQuery({
    queryKey: ['task-activity', task.id, showToolLog ? 'all' : 'milestone'],
    queryFn: () => planTreeApi.getTaskActivity(task.id, showToolLog ? 'all' : 'milestone'),
  })

  const upstreamMap = useMemo(() => {
    const map = new Map<string, { task: PlanTaskNode; phaseName: string }>()
    for (const p of tree.phases) {
      for (const t of p.tasks) map.set(t.id, { task: t, phaseName: p.name })
    }
    return map
  }, [tree])

  const elapsed = task.started_at ? formatRelativeFrom(task.started_at) : null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-stitch-outline-variant/10 px-6 pt-6 pb-4">
        <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-text-tertiary">
          {phase.name}
        </div>
        <div className="mb-3 flex items-start justify-between gap-3">
          <h2 className="text-xl font-bold text-text-primary leading-tight">{task.title}</h2>
          <span className={`shrink-0 rounded-md px-2 py-1 text-[10px] font-bold uppercase tracking-wider ${STATUS_BADGE[task.status]}`}>
            {STATUS_LABEL[task.status]}
          </span>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-tertiary">
          {task.agent_name && <span>Agent: <span className="text-text-secondary">{task.agent_name}</span></span>}
          <span>Priority: <span className="text-text-secondary">{task.priority}</span></span>
          <span>Type: <span className="text-text-secondary">{task.task_type}</span></span>
          {elapsed && <span>{elapsed}</span>}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-6">
        {task.depends_on_ids.length > 0 && (
          <Section title="Dependencies">
            <div className="flex flex-col gap-1">
              {task.depends_on_ids.map((depId) => {
                const upstream = upstreamMap.get(depId)
                if (!upstream) return null
                return (
                  <div
                    key={depId}
                    className="flex items-center gap-2 rounded border border-stitch-outline-variant/10 bg-stitch-surface-low px-2 py-1.5"
                  >
                    <span className="text-[11px] text-text-secondary truncate flex-1">
                      {upstream.task.title}
                    </span>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${STATUS_BADGE[upstream.task.status]}`}>
                      {upstream.task.status}
                    </span>
                  </div>
                )
              })}
            </div>
          </Section>
        )}

        <Section
          title="Activity"
          action={
            <button
              type="button"
              onClick={() => setShowToolLog((prev) => !prev)}
              className="text-[10px] font-bold uppercase tracking-wider text-stitch-primary hover:underline"
            >
              {showToolLog ? 'Hide tool log' : 'Show tool log'}
            </button>
          }
        >
          {activityQuery.isLoading ? (
            <p className="text-xs text-text-tertiary">Loading…</p>
          ) : activityQuery.data && activityQuery.data.entries.length > 0 ? (
            <div className="flex flex-col gap-2">
              {activityQuery.data.entries.map((entry) => (
                <ActivityRow key={entry.id} entry={entry} />
              ))}
            </div>
          ) : (
            <p className="text-xs text-text-tertiary italic">No activity recorded yet.</p>
          )}
        </Section>
      </div>
    </div>
  )
}

function Section({
  title,
  action,
  children,
}: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary">{title}</h3>
        {action}
      </div>
      {children}
    </div>
  )
}

function ActivityRow({ entry }: { entry: ActivityEntry }) {
  const dotColor =
    entry.level === 'tool'
      ? 'bg-stitch-outline-variant/40'
      : entry.event_type === 'task_completed'
        ? 'bg-stitch-primary'
        : entry.event_type === 'task_blocked'
          ? 'bg-rose-500'
          : 'bg-emerald-400'

  return (
    <div className="flex items-start gap-2">
      <div className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-xs text-text-secondary truncate">{entry.summary}</span>
          <span className="shrink-0 text-[10px] text-text-tertiary tabular-nums">
            {formatTime(entry.created_at)}
          </span>
        </div>
        {entry.event_type !== 'state_transition' && (
          <span className="text-[9px] text-text-tertiary uppercase tracking-wider">
            {entry.event_type}
          </span>
        )}
      </div>
    </div>
  )
}


// ── Helpers ─────────────────────────────────────────────────────────


function formatTime(iso: string): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

function formatRelativeFrom(iso: string): string {
  try {
    const started = new Date(iso).getTime()
    const diff = Date.now() - started
    const min = Math.floor(diff / 60000)
    if (min < 1) return 'just started'
    if (min < 60) return `running ${min}m`
    const hr = Math.floor(min / 60)
    return `running ${hr}h ${min % 60}m`
  } catch {
    return ''
  }
}
