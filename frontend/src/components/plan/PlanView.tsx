import { useEffect } from 'react'
import { usePlanEvents } from '../../hooks/usePlanEvents'
import { usePlanStore } from '../../stores/planStore'
import AgentStatusBar from './AgentStatusBar'
import DetailPanel from './DetailPanel'
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

  return (
    <div className="flex h-full flex-col">
      <AgentStatusBar projectId={projectId} />
      <div className="flex flex-1 overflow-hidden">
        <div className="flex w-[35%] min-w-[280px] max-w-[480px] flex-col border-r border-stitch-outline-variant/10">
          <PlanTree projectId={projectId} />
        </div>
        <div className="flex-1 overflow-hidden">
          <DetailPanel projectId={projectId} />
        </div>
      </div>
    </div>
  )
}
