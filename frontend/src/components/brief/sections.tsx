'use client'

import { Badge } from '../common/Badge'
import { DeliverableCard } from './DeliverableCard'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import type {
  BriefBlockedItem,
  BriefDecision,
  BriefRequest,
} from '../../types/founder'

/**
 * Building blocks for the per-section ProjectPage tabs (G7.5d).
 *
 * The Brief still owns the data shape (`briefApi.forProject().sections`);
 * each tab renders one section using the row components below.
 */

export function Section({
  title,
  count,
  emptyText,
  tone,
  children,
}: {
  title?: string
  count: number
  emptyText: string
  tone?: 'rose'
  children: React.ReactNode
}) {
  return (
    <section>
      {title != null && (
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
      )}
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

export function DecisionRow({ d }: { d: BriefDecision }) {
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

export function RequestRow({ r }: { r: BriefRequest }) {
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

export function BlockedRow({ item }: { item: BriefBlockedItem }) {
  if (item.kind === 'request') {
    return <RequestRow r={item} />
  }
  return <DeliverableCard d={item} />
}

export { DeliverableCard }
