import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { planProposalsApi, type ApprovalSettings } from '../../api/planProposals'

const LABELS: Record<string, string> = {
  auto_approve: 'Auto',
  require_approval: 'Approval',
}

const DESCRIPTIONS: Record<string, string> = {
  auto_approve: 'Agents create directly',
  require_approval: 'Agents propose, user approves',
}

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
    <div className="flex items-center gap-3 text-[11px]">
      {fields.map(({ key, label }) => (
        <div key={key} className="flex items-center gap-1">
          <span className="text-text-tertiary">{label}</span>
          <div className="flex gap-0.5">
            {(['auto_approve', 'require_approval'] as const).map((level) => (
              <button
                key={level}
                onClick={() => mutation.mutate({ [key]: level })}
                title={DESCRIPTIONS[level]}
                className={`px-1.5 py-0.5 rounded transition-colors ${
                  settings[key] === level
                    ? 'bg-stitch-primary text-stitch-on-primary font-bold'
                    : 'bg-stitch-surface-highest text-text-tertiary hover:text-text-secondary'
                }`}
              >
                {LABELS[level]}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
