import { useMutation, useQueryClient } from '@tanstack/react-query'
import { agentControlApi } from '../../api/agentControl'

interface StopAllButtonProps {
  projectId: string
}

export default function StopAllButton({ projectId }: StopAllButtonProps) {
  const queryClient = useQueryClient()
  const stopAll = useMutation({
    mutationFn: () => agentControlApi.stopAll(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] })
    },
  })

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
