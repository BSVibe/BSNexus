'use client'

import { useTranslations } from 'next-intl'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { Badge } from '../common/Badge'
import { ProofBadge } from '../common/ProofBadge'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
// truncId is used below in the deliverable id badge.
import { deliverablesApi } from '../../api/founder'
import type { BriefDeliverable, DeliverableType } from '../../types/founder'

const TYPE_COLOR: Record<DeliverableType, string> = {
  code: '#93c5fd',
  doc: '#6ee7b7',
  design: '#fda4af',
  data: '#fcd34d',
  url: '#93c5fd',
}

function DeliverableTypeIcon({ t }: { t: DeliverableType }) {
  const icons: Record<DeliverableType, React.ReactNode> = {
    code: <I.Code size={12} />,
    doc: <I.Doc size={12} />,
    design: <I.Design size={12} />,
    data: <I.Data size={12} />,
    url: <I.Url size={12} />,
  }
  return <span style={{ color: TYPE_COLOR[t] }}>{icons[t]}</span>
}

/**
 * DeliverableCard — used in the Brief shipped section and elsewhere.
 *
 * Surfaces:
 * - title + type
 * - ProofBadge (decision-locks A1) — primary signal
 * - proof_summary one-liner (verifier exit code + tail)
 * - relTime + truncated request_id
 * - "Re-verify" action when the deliverable has a verifier_type
 *   configured (POST /api/v1/deliverables/{id}/verify).
 */
export function DeliverableCard({ d }: { d: BriefDeliverable }) {
  const t = useTranslations('nexus.brief')
  const queryClient = useQueryClient()
  const verifyMutation = useMutation({
    mutationFn: () => deliverablesApi.verify(d.id),
    onSuccess: () => {
      // Invalidate everything that mirrors Deliverable proof state so
      // the Brief / Deliverable list / project page all refetch and the
      // SSE channel can take it from there.
      queryClient.invalidateQueries({ queryKey: ['brief'] })
      queryClient.invalidateQueries({ queryKey: ['deliverables'] })
    },
  })

  const canVerify = Boolean(d.verifier_type)

  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <DeliverableTypeIcon t={d.type} />
        <span
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: 'var(--gray-50)',
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {d.title}
        </span>
        <ProofBadge state={d.proof_state} />
      </div>

      {d.proof_summary && (
        <div
          className="mono"
          style={{
            fontSize: 11,
            color: 'var(--text-secondary)',
            marginBottom: 8,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={d.proof_summary}
        >
          {d.proof_summary}
        </div>
      )}

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          fontSize: 11,
          color: 'var(--text-tertiary)',
        }}
      >
        <span>{relTime(d.created_at)}</span>
        {d.verifier_type && (
          <>
            <span>·</span>
            <Badge tone="gray">{d.verifier_type}</Badge>
          </>
        )}
        <span style={{ flex: 1 }} />
        {canVerify && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => verifyMutation.mutate()}
            disabled={verifyMutation.isPending}
            aria-label={t('deliverable.verifyAria')}
          >
            <I.Refresh size={12} />
            <span style={{ marginLeft: 4 }}>{t('deliverable.verifyButton')}</span>
          </button>
        )}
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => navigator.clipboard.writeText(d.id)}
          title={t('copyDeliverableId')}
          aria-label={t('copyDeliverableId')}
        >
          <I.Copy size={12} />
        </button>
        <span className="mono" title={d.id}>
          {truncId(d.id)}
        </span>
      </div>
    </div>
  )
}
