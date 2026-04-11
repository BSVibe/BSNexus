import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { planProposalsApi, type PlanProposal } from '../../api/planProposals'

interface ProposalBannerProps {
  projectId: string
}

export default function ProposalBanner({ projectId }: ProposalBannerProps) {
  const queryClient = useQueryClient()
  const { data: proposals = [] } = useQuery({
    queryKey: ['proposals', projectId],
    queryFn: () => planProposalsApi.list(projectId),
    refetchInterval: 5000,
  })

  const approveMutation = useMutation({
    mutationFn: (id: string) => planProposalsApi.approve(projectId, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['proposals', projectId] })
      queryClient.invalidateQueries({ queryKey: ['plan-tree', projectId] })
    },
  })

  const rejectMutation = useMutation({
    mutationFn: (id: string) => planProposalsApi.reject(projectId, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['proposals', projectId] })
    },
  })

  if (proposals.length === 0) return null

  return (
    <div className="mx-4 mb-2">
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2">
        <p className="text-[11px] font-bold text-amber-400 mb-2">
          {proposals.length} pending proposal{proposals.length > 1 ? 's' : ''}
        </p>
        <div className="space-y-2">
          {proposals.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              onApprove={() => approveMutation.mutate(p.id)}
              onReject={() => rejectMutation.mutate(p.id)}
              loading={approveMutation.isPending || rejectMutation.isPending}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function ProposalCard({
  proposal,
  onApprove,
  onReject,
  loading,
}: {
  proposal: PlanProposal
  onApprove: () => void
  onReject: () => void
  loading: boolean
}) {
  const title =
    proposal.proposal_type === 'phase'
      ? (proposal.payload as { name?: string }).name || 'Untitled Phase'
      : (proposal.payload as { title?: string }).title || 'Untitled Task'
  const desc =
    proposal.proposal_type === 'phase'
      ? (proposal.payload as { description?: string }).description
      : (proposal.payload as { description?: string }).description

  return (
    <div className="flex items-start gap-2 bg-stitch-surface-low rounded-md px-2.5 py-2 border border-stitch-outline-variant/10">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${
            proposal.proposal_type === 'phase'
              ? 'bg-stitch-primary/10 text-stitch-primary'
              : 'bg-emerald-500/10 text-emerald-400'
          }`}>
            {proposal.proposal_type}
          </span>
          <span className="text-xs font-medium text-text-primary truncate">{title}</span>
        </div>
        {desc && (
          <p className="text-[10px] text-text-tertiary truncate">{desc}</p>
        )}
        {proposal.proposer_agent_name && (
          <p className="text-[9px] text-text-tertiary mt-0.5">
            by {proposal.proposer_agent_name}
          </p>
        )}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <button
          onClick={onApprove}
          disabled={loading}
          className="text-[10px] px-2 py-1 rounded bg-emerald-500/15 text-emerald-400 font-bold hover:bg-emerald-500/25 disabled:opacity-50 transition-colors"
        >
          Approve
        </button>
        <button
          onClick={onReject}
          disabled={loading}
          className="text-[10px] px-2 py-1 rounded bg-rose-500/15 text-rose-400 font-bold hover:bg-rose-500/25 disabled:opacity-50 transition-colors"
        >
          Reject
        </button>
      </div>
    </div>
  )
}
