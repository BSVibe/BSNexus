import { useEffect } from 'react'
import { usePlanEvents } from '../../hooks/usePlanEvents'
import { usePlanStore } from '../../stores/planStore'
import AgentStatusBar from './AgentStatusBar'
import PlanTree from './PlanTree'

interface PlanViewProps {
  projectId: string
}

export default function PlanView({ projectId }: PlanViewProps) {
  // Stream task transitions and agent status updates into the React Query cache.
  usePlanEvents(projectId)

  // Reset selection when switching projects so we never carry over a stale node id.
  const reset = usePlanStore((s) => s.reset)
  useEffect(() => {
    reset()
    return () => reset()
  }, [projectId, reset])

  const selectedNode = usePlanStore((s) => s.selectedNode)

  return (
    <div className="flex h-full flex-col">
      <AgentStatusBar projectId={projectId} />
      <div className="flex flex-1 overflow-hidden">
        <div className="flex w-[35%] min-w-[280px] max-w-[480px] flex-col border-r border-stitch-outline-variant/10">
          <PlanTree projectId={projectId} />
        </div>
        <div className="flex-1 overflow-hidden">
          <DetailPanelPlaceholder selection={selectedNode} />
        </div>
      </div>
    </div>
  )
}

function DetailPanelPlaceholder({
  selection,
}: {
  selection: { type: 'phase' | 'task'; id: string } | null
}) {
  if (!selection) {
    return (
      <div className="flex h-full items-center justify-center text-text-tertiary text-sm">
        Select a phase or task in the tree to see details
      </div>
    )
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-text-tertiary text-sm">
      <span className="material-symbols-outlined" style={{ fontSize: '32px' }}>
        construction
      </span>
      <p>Detail panel for {selection.type} coming in Phase 2</p>
      <p className="text-[10px] opacity-60">{selection.id}</p>
    </div>
  )
}
