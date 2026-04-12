import { useMutation } from '@tanstack/react-query'
import { agentControlApi } from '../../api/agentControl'

interface StopAllButtonProps {
  projectId: string
}

export default function StopAllButton({ projectId }: StopAllButtonProps) {
  const stopAll = useMutation({
    mutationFn: () => agentControlApi.stopAll(projectId),
  })

  return (
    <button
      type="button"
      onClick={() => stopAll.mutate()}
      disabled={stopAll.isPending}
      className="flex items-center gap-1.5 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs font-bold text-rose-400 transition-colors hover:bg-rose-500/20 disabled:opacity-50"
    >
      <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
        stop_circle
      </span>
      {stopAll.isPending ? '중지 중...' : '모든 에이전트 중지'}
    </button>
  )
}
