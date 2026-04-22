import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { agentControlApi } from '../../api/agentControl'
import { planTreeApi } from '../../api/planTree'

interface StopAllButtonProps {
  projectId: string
}

export default function StopAllButton({ projectId }: StopAllButtonProps) {
  const queryClient = useQueryClient()

  // Check if any agent is currently running in this project
  const { data: agentStatus = [] } = useQuery({
    queryKey: ['agent-status', projectId],
    queryFn: () => planTreeApi.getAgentStatus(projectId),
    refetchInterval: 5000,
  })
  const hasRunning = agentStatus.some((a) => a.current_task?.status === 'running')

  const stopAll = useMutation({
    mutationFn: () => agentControlApi.stopAll(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] })
      queryClient.invalidateQueries({ queryKey: ['agent-status', projectId] })
      queryClient.invalidateQueries({ queryKey: ['plan-tree', projectId] })
    },
  })

  const restart = useMutation({
    mutationFn: () => agentControlApi.restart(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] })
      queryClient.invalidateQueries({ queryKey: ['agent-status', projectId] })
      queryClient.invalidateQueries({ queryKey: ['plan-tree', projectId] })
    },
  })

  if (hasRunning) {
    return (
      <button
        type="button"
        onClick={() => stopAll.mutate()}
        disabled={stopAll.isPending}
        className="shrink-0 rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-[10px] font-bold text-rose-400 transition-colors hover:bg-rose-500/20 disabled:opacity-50"
      >
        {stopAll.isPending ? '...' : '중지'}
      </button>
    )
  }

  return (
    <button
      type="button"
      onClick={() => restart.mutate()}
      disabled={restart.isPending}
      className="shrink-0 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-[10px] font-bold text-emerald-400 transition-colors hover:bg-emerald-500/20 disabled:opacity-50"
    >
      {restart.isPending ? '...' : '재시작'}
    </button>
  )
}
