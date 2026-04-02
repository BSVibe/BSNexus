import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { pmApi } from '../../api/pm'
import { Badge } from '../common'

interface Props {
  projectId: string
}

interface PMStatus {
  running: boolean
}

export default function PMControl({ projectId }: Props) {
  const queryClient = useQueryClient()
  const [logs, setLogs] = useState<string[]>([])

  const addLog = (message: string) => {
    setLogs((prev) => [message, ...prev].slice(0, 5))
  }

  const { data: status } = useQuery<PMStatus>({
    queryKey: ['pm-status', projectId],
    queryFn: () => pmApi.status(projectId),
    refetchInterval: 5000,
  })

  const startMutation = useMutation({
    mutationFn: () => pmApi.start(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pm-status', projectId] })
      addLog('PM started')
    },
    onError: () => addLog('Failed to start PM'),
  })

  const pauseMutation = useMutation({
    mutationFn: () => pmApi.pause(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pm-status', projectId] })
      addLog('PM paused')
    },
    onError: () => addLog('Failed to pause PM'),
  })

  const queueMutation = useMutation({
    mutationFn: () => pmApi.queueNext(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['board', projectId] })
      addLog('Queued next task')
    },
    onError: () => addLog('No tasks to queue'),
  })

  const isRunning = status?.running ?? false

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-bold uppercase tracking-widest text-text-secondary">PM</span>
        {isRunning ? (
          <Badge color="in_progress" label="Running" />
        ) : (
          <Badge color="waiting" label="Paused" />
        )}
      </div>

      <div className="flex gap-2">
        {isRunning ? (
          <button
            onClick={() => pauseMutation.mutate()}
            disabled={pauseMutation.isPending}
            className="bg-stitch-surface-highest text-text-primary px-3 py-1.5 rounded-md text-xs font-semibold hover:opacity-80 transition-opacity disabled:opacity-50 flex items-center gap-1.5"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>pause</span>
            Pause
          </button>
        ) : (
          <button
            onClick={() => startMutation.mutate()}
            disabled={startMutation.isPending}
            className="bg-stitch-surface-highest text-text-primary px-3 py-1.5 rounded-md text-xs font-semibold hover:opacity-80 transition-opacity disabled:opacity-50 flex items-center gap-1.5"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>play_arrow</span>
            Start
          </button>
        )}
        <button
          onClick={() => queueMutation.mutate()}
          disabled={queueMutation.isPending}
          className="bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container px-3 py-1.5 rounded-md text-xs font-bold shadow-lg shadow-stitch-primary/20 hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center gap-1.5"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>queue</span>
          Queue Next
        </button>
      </div>

      {logs.length > 0 && (
        <div className="flex items-center gap-2 text-[10px] text-text-tertiary">
          <span className="material-symbols-outlined" style={{ fontSize: '12px' }}>info</span>
          {logs[0]}
        </div>
      )}
    </div>
  )
}
