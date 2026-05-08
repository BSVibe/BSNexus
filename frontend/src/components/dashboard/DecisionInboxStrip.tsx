'use client'

import { useTranslations } from 'next-intl'
import { useRouter } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'

import { Badge } from '../common/Badge'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import { decisionsApi } from '../../api/founder'
import type { Decision } from '../../types/founder'

/**
 * DecisionInboxStrip — Home-level cross-project blocking decisions.
 *
 * Locked priority slot in core-ux-spec §Default Home Layout: Decision
 * Inbox strip is rendered first, before Brief / Shipped / Active.
 * Reads ``GET /api/v1/decisions?blocking_only=true`` (no ``project_id``)
 * — A3 lets the same endpoint serve project + cross-project shapes.
 */
export function DecisionInboxStrip() {
  const t = useTranslations('nexus.decisionInbox')
  const router = useRouter()

  const { data: decisions = [] } = useQuery<Decision[]>({
    queryKey: ['decisions', 'inbox', 'blocking'],
    queryFn: () => decisionsApi.list({ blockingOnly: true, limit: 5 }),
  })

  return (
    <section
      className="card"
      style={{
        padding: 16,
        marginBottom: 24,
        borderColor: decisions.length > 0 ? 'var(--color-rose)' : undefined,
      }}
      data-decision-inbox-strip
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <I.Inbox size={14} />
        <h2 style={{ fontSize: 14, fontWeight: 600, color: 'var(--gray-50)', margin: 0, flex: 1 }}>
          {t('heading')}
        </h2>
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
          {decisions.length}
        </span>
      </div>

      {decisions.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('empty')}</div>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {decisions.map((d) => (
            <li key={d.id}>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => router.push(`/projects/${d.project_id}?tab=decisions`)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  width: '100%',
                  padding: 10,
                  textAlign: 'left',
                  fontWeight: 400,
                  fontSize: 13,
                  color: 'var(--gray-50)',
                }}
              >
                <Badge tone="rose">{t('blockingBadge')}</Badge>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {d.question}
                </span>
                <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  {truncId(d.project_id)}
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  {relTime(d.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
