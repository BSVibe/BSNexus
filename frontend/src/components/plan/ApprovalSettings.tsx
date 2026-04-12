import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { planProposalsApi, type ApprovalSettings } from '../../api/planProposals'

interface Props {
  projectId: string
}

export default function ApprovalSettingsPanel({ projectId }: Props) {
  const queryClient = useQueryClient()
  const { data: settings } = useQuery({
    queryKey: ['approval-settings', projectId],
    queryFn: () => planProposalsApi.getSettings(projectId),
  })

  const mutation = useMutation({
    mutationFn: (update: Partial<ApprovalSettings>) =>
      planProposalsApi.updateSettings(projectId, update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['approval-settings', projectId] })
    },
  })

  if (!settings) return null

  const fields: Array<{ key: keyof ApprovalSettings; label: string }> = [
    { key: 'phase_creation', label: 'Phase' },
    { key: 'task_creation', label: 'Task' },
  ]

  return (
    <div className="flex items-center gap-2 text-[10px]">
      {fields.map(({ key, label }) => {
        const isAuto = settings[key] === 'auto_approve'
        return (
          <button
            key={key}
            onClick={() => mutation.mutate({ [key]: isAuto ? 'require_approval' : 'auto_approve' })}
            title={`${label}: ${isAuto ? 'Auto (click for approval)' : 'Approval required (click for auto)'}`}
            className={`px-1.5 py-0.5 rounded transition-colors ${
              isAuto
                ? 'bg-stitch-surface-highest text-text-tertiary'
                : 'bg-amber-500/20 text-amber-400 font-bold'
            }`}
          >
            {label} {isAuto ? 'Auto' : '승인'}
          </button>
        )
      })}
    </div>
  )
}
