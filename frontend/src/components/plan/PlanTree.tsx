import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  planTreeApi,
  type PhaseStatus,
  type PlanPhaseNode,
  type PlanTaskNode,
  type TaskStatus,
} from '../../api/planTree'
import { usePlanStore } from '../../stores/planStore'

const TASK_STATUS_ICON: Record<TaskStatus, string> = {
  pending: 'pause_circle',
  running: 'play_arrow',
  blocked: 'warning',
  done: 'check_circle',
}

const TASK_STATUS_COLOR: Record<TaskStatus, string> = {
  pending: 'text-text-tertiary',
  running: 'text-emerald-400',
  blocked: 'text-rose-500',
  done: 'text-stitch-primary',
}

const PHASE_STATUS_BADGE: Record<PhaseStatus, string> = {
  pending: 'bg-stitch-outline-variant/10 text-text-tertiary',
  active: 'bg-emerald-500/10 text-emerald-400',
  completed: 'bg-stitch-primary/10 text-stitch-primary',
}

interface PlanTreeProps {
  projectId: string
}

export default function PlanTree({ projectId }: PlanTreeProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['plan-tree', projectId],
    queryFn: () => planTreeApi.getTree(projectId),
    enabled: !!projectId,
  })
  const expandedPhases = usePlanStore((s) => s.expandedPhases)
  const togglePhase = usePlanStore((s) => s.togglePhase)
  const expandPhase = usePlanStore((s) => s.expandPhase)
  const selectedNode = usePlanStore((s) => s.selectedNode)
  const selectNode = usePlanStore((s) => s.selectNode)
  const highlightedAgentId = usePlanStore((s) => s.highlightedAgentId)

  // Auto-expand the active phase the first time the tree loads.
  useEffect(() => {
    if (!data) return
    const active = data.phases.find((p) => p.status === 'active')
    if (active) expandPhase(active.id)
  }, [data, expandPhase])

  if (isLoading) {
    return (
      <div className="p-6 text-text-tertiary text-sm">Loading plan…</div>
    )
  }

  if (error || !data) {
    return (
      <div className="p-6 text-rose-400 text-sm">Failed to load plan tree.</div>
    )
  }

  if (data.phases.length === 0) {
    return (
      <div className="p-6 text-text-tertiary text-sm">
        No phases yet. Start a project chat to design the first phase.
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {data.goal && (
        <div className="px-4 py-3 border-b border-stitch-outline-variant/10">
          <div className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary mb-1">
            Goal
          </div>
          <div className="text-sm text-text-primary leading-snug">{data.goal}</div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-2 py-3">
        {data.phases.map((phase) => (
          <PhaseNode
            key={phase.id}
            phase={phase}
            expanded={expandedPhases.has(phase.id)}
            onToggle={() => togglePhase(phase.id)}
            selectedNodeId={selectedNode?.id ?? null}
            selectedNodeType={selectedNode?.type ?? null}
            onSelect={(type, id) => selectNode({ type, id })}
            highlightedAgentId={highlightedAgentId}
          />
        ))}
      </div>
    </div>
  )
}

interface PhaseNodeProps {
  phase: PlanPhaseNode
  expanded: boolean
  onToggle: () => void
  selectedNodeId: string | null
  selectedNodeType: 'phase' | 'task' | null
  onSelect: (type: 'phase' | 'task', id: string) => void
  highlightedAgentId: string | null
}

function PhaseNode({
  phase,
  expanded,
  onToggle,
  selectedNodeId,
  selectedNodeType,
  onSelect,
  highlightedAgentId,
}: PhaseNodeProps) {
  const isSelected = selectedNodeType === 'phase' && selectedNodeId === phase.id
  const taskCount = phase.tasks.length
  const doneCount = phase.tasks.filter((t) => t.status === 'done').length

  return (
    <div className="mb-1">
      <div
        className={`group flex items-center gap-1 rounded-md px-2 py-1.5 cursor-pointer transition-colors ${
          isSelected ? 'bg-stitch-primary/10' : 'hover:bg-stitch-surface-low'
        }`}
        onClick={() => onSelect('phase', phase.id)}
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onToggle()
          }}
          className="flex h-5 w-5 items-center justify-center text-text-tertiary hover:text-text-primary"
          aria-label={expanded ? 'Collapse phase' : 'Expand phase'}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
            {expanded ? 'expand_more' : 'chevron_right'}
          </span>
        </button>
        <span className="text-sm font-bold text-text-primary truncate flex-1">{phase.name}</span>
        <span
          className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${PHASE_STATUS_BADGE[phase.status]}`}
        >
          {phase.status}
        </span>
        <span className="shrink-0 text-[10px] text-text-tertiary tabular-nums">
          {doneCount}/{taskCount}
        </span>
      </div>

      {expanded && (
        <div className="ml-6 border-l border-stitch-outline-variant/15 pl-2">
          {phase.tasks.length === 0 ? (
            <div className="px-2 py-1 text-[11px] text-text-tertiary italic">No tasks yet</div>
          ) : (
            phase.tasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                isSelected={selectedNodeType === 'task' && selectedNodeId === task.id}
                onSelect={() => onSelect('task', task.id)}
                isHighlighted={highlightedAgentId !== null && task.agent_id === highlightedAgentId}
              />
            ))
          )}
        </div>
      )}
    </div>
  )
}

interface TaskRowProps {
  task: PlanTaskNode
  isSelected: boolean
  onSelect: () => void
  isHighlighted: boolean
}

function TaskRow({ task, isSelected, onSelect, isHighlighted }: TaskRowProps) {
  return (
    <div
      onClick={onSelect}
      className={`flex items-center gap-2 rounded px-2 py-1 cursor-pointer transition-colors ${
        isSelected
          ? 'bg-stitch-primary/10'
          : isHighlighted
            ? 'bg-amber-500/10'
            : 'hover:bg-stitch-surface-low'
      }`}
    >
      <span
        className={`material-symbols-outlined shrink-0 ${TASK_STATUS_COLOR[task.status]}`}
        style={{ fontSize: '14px' }}
      >
        {TASK_STATUS_ICON[task.status]}
      </span>
      <span className="text-xs text-text-secondary truncate flex-1">{task.title}</span>
      {task.agent_name && (
        <span className="shrink-0 rounded bg-stitch-surface-container px-1.5 py-0.5 text-[9px] text-text-tertiary uppercase">
          {task.agent_name}
        </span>
      )}
      {task.depends_on_ids.length > 0 && (
        <span
          className="material-symbols-outlined shrink-0 text-text-tertiary"
          style={{ fontSize: '12px' }}
          title={`Depends on ${task.depends_on_ids.length} task(s)`}
        >
          link
        </span>
      )}
    </div>
  )
}
