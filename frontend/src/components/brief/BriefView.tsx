'use client'

import { useTranslations } from 'next-intl'
import { useQuery } from '@tanstack/react-query'

import { Badge } from '../common/Badge'
import { DeliverableCard } from './DeliverableCard'
import { briefApi } from '../../api/brief'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import type {
  BriefBlockedItem,
  BriefDecision,
  BriefRequest,
  BriefResponse,
} from '../../types/founder'

/**
 * Brief — locked summary surface (decision-locks O5 trigger reached
 * with PR4: timeline becomes a subsection, the lead is the 5 founder
 * cards). Replaces the old Progress timeline-only view.
 *
 * G7.1 — wire shape is `data.sections.{shipped, needs_decision,
 * blocked, running, next}` (typed). Blocked is a discriminated union;
 * we narrow on `kind` to render either a request row or a deliverable
 * card.
 *
 * Section order matches core-ux-spec §Brief UX:
 * 1. Shipped
 * 2. Needs your decision
 * 3. Blocked
 * 4. Running
 * 5. Next
 */
export default function BriefView({ projectId }: { projectId: string }) {
  const t = useTranslations('nexus.brief')

  const { data, isLoading } = useQuery<BriefResponse>({
    queryKey: ['brief', projectId],
    queryFn: () => briefApi.forProject(projectId, { limit: 10 }),
  })

  if (isLoading || !data) {
    return (
      <div style={{ padding: 24, color: 'var(--text-tertiary)', fontSize: 13 }}>
        {t('loading')}
      </div>
    )
  }

  const { shipped, needs_decision, blocked, running, next } = data.sections

  return (
    <div className="brief-view" data-brief-surface style={{ overflow: 'auto', height: '100%' }}>
      <div className="brief-view__inner" style={{ maxWidth: 900, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 28 }}>
        <Section title={t('section.shipped')} count={shipped.length} emptyText={t('section.shippedEmpty')}>
          {shipped.map((d) => (
            <DeliverableCard key={d.id} d={d} />
          ))}
        </Section>

        <Section
          title={t('section.needsDecision')}
          count={needs_decision.length}
          emptyText={t('section.needsDecisionEmpty')}
          tone="rose"
        >
          {needs_decision.map((d) => (
            <DecisionRow key={d.id} d={d} />
          ))}
        </Section>

        <Section title={t('section.blocked')} count={blocked.length} emptyText={t('section.blockedEmpty')}>
          {blocked.map((item) => (
            <BlockedRow key={`${item.kind}-${item.id}`} item={item} />
          ))}
        </Section>

        <Section title={t('section.running')} count={running.length} emptyText={t('section.runningEmpty')}>
          {running.map((r) => (
            <RequestRow key={r.id} r={r} />
          ))}
        </Section>

        <Section title={t('section.next')} count={next.length} emptyText={t('section.nextEmpty')}>
          {next.map((n, i) => (
            <div key={i} className="card" style={{ padding: 14, fontSize: 13, color: 'var(--gray-100)' }}>
              {n.summary}
            </div>
          ))}
        </Section>
      </div>
    </div>
  )
}

function Section({
  title,
  count,
  emptyText,
  tone,
  children,
}: {
  title: string
  count: number
  emptyText: string
  tone?: 'rose'
  children: React.ReactNode
}) {
  return (
    <section>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <h2
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: 'var(--gray-50)',
            margin: 0,
          }}
        >
          {title}
        </h2>
        <span
          className="mono"
          style={{
            fontSize: 11,
            color: tone === 'rose' && count > 0 ? 'var(--color-rose)' : 'var(--text-tertiary)',
          }}
        >
          {count}
        </span>
      </div>
      {count === 0 ? (
        <div className="card" style={{ padding: 16, fontSize: 12, color: 'var(--text-tertiary)' }}>
          {emptyText}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>{children}</div>
      )}
    </section>
  )
}

function DecisionRow({ d }: { d: BriefDecision }) {
  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <I.Inbox size={12} />
        <span style={{ fontSize: 13, color: 'var(--gray-50)', flex: 1 }}>{d.question}</span>
        {d.blocking && <Badge tone="rose">blocking</Badge>}
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{relTime(d.created_at)}</div>
    </div>
  )
}

function RequestRow({ r }: { r: BriefRequest }) {
  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <I.Timeline size={12} />
        <span style={{ fontSize: 13, color: 'var(--gray-50)', flex: 1 }}>
          {r.intent || truncId(r.id)}
        </span>
        <Badge tone={r.status === 'blocked' ? 'rose' : 'blue'} dot>
          {r.status}
        </Badge>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{relTime(r.updated_at)}</div>
    </div>
  )
}

function BlockedRow({ item }: { item: BriefBlockedItem }) {
  if (item.kind === 'request') {
    return <RequestRow r={item} />
  }
  // kind === 'deliverable' — render the same DeliverableCard the
  // shipped section uses; the proof badge differentiates the failure.
  return <DeliverableCard d={item} />
}
