'use client'

import { useTranslations } from 'next-intl'
import { ResponsiveTable } from '@bsvibe/ui'
import type { ResponsiveTableColumn } from '@bsvibe/ui'

import { Badge } from '../common/Badge'
import { DeliverableCard } from './DeliverableCard'
import { ProofBadge } from '../common/ProofBadge'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import type {
  BriefBlockedItem,
  BriefDecision,
  BriefDeliverable,
  BriefRequest,
} from '../../types/founder'

/**
 * Building blocks for the per-section ProjectPage tabs (G7.5d).
 *
 * The Brief still owns the data shape (`briefApi.forProject().sections`);
 * each tab renders one section using the row components below.
 *
 * Tabular adoption: the per-section lists now render through the shared
 * `<ResponsiveTable>` (`@bsvibe/ui`). Desktop (`sm:`+) gets a real
 * `<table>`; mobile keeps the existing card components via
 * `renderMobileCard` so the founder card look doesn't regress.
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
      <SectionHeader title={title} count={count} tone={tone} />
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

function SectionHeader({
  title,
  count,
  tone,
}: {
  title?: string
  count: number
  tone?: 'rose'
}) {
  if (title == null) return null
  return (
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
  )
}

/**
 * TableSection — Section chrome (title + count + empty-state) over a
 * `<ResponsiveTable>`. Used by the Brief tabs so the desktop side gets a
 * real table while mobile keeps the row cards via `renderMobileCard`.
 */
export function TableSection<TRow>({
  title,
  count,
  tone,
  emptyText,
  columns,
  rows,
  rowKey,
  renderMobileCard,
}: {
  title?: string
  count: number
  tone?: 'rose'
  emptyText: string
  columns: readonly ResponsiveTableColumn<TRow>[]
  rows: readonly TRow[]
  rowKey: (row: TRow) => string
  renderMobileCard: (row: TRow, index: number) => React.ReactNode
}) {
  return (
    <section>
      <SectionHeader title={title} count={count} tone={tone} />
      <ResponsiveTable
        columns={columns}
        rows={rows}
        rowKey={rowKey}
        renderMobileCard={renderMobileCard}
        emptyMessage={emptyText}
      />
    </section>
  )
}

export function DecisionRow({ d }: { d: BriefDecision }) {
  const t = useTranslations('nexus.decisions')
  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <I.Inbox size={12} />
        <span style={{ fontSize: 13, color: 'var(--gray-50)', flex: 1 }}>{d.question}</span>
        {d.blocking && <Badge tone="rose">{t('blockingBadge')}</Badge>}
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
        <Badge tone={r.status === 'needs_decision' ? 'rose' : 'blue'} dot>
          {r.status}
        </Badge>
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 11,
          color: 'var(--text-tertiary)',
        }}
      >
        <span>{relTime(r.updated_at)}</span>
        {r.pr_number && r.pr_url && (
          <>
            <span>·</span>
            <a
              href={r.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: 'var(--blue-500)', textDecoration: 'none' }}
              title={r.pr_url}
            >
              PR #{r.pr_number}
            </a>
          </>
        )}
      </div>
    </div>
  )
}

export function BlockedRow({ item }: { item: BriefBlockedItem }) {
  // The ``blocked`` section is deliverable-only — a stalled Request now
  // surfaces in ``needs_decision`` via its open founder Decision.
  return <DeliverableCard d={item} />
}

// ─── ResponsiveTable column models ───────────────────────────────────
//
// `blocked` rows are deliverables whose proof failed/missing — the
// desktop table surfaces the same data the row cards do, and the mobile
// renderer reuses the cards verbatim.

/** Shared inline-link styling for PR / commit anchors inside table cells. */
function stopAnchorPropagation(e: React.MouseEvent) {
  e.stopPropagation()
}

export function useRequestColumns(): ResponsiveTableColumn<BriefRequest>[] {
  const t = useTranslations('nexus.brief')
  return [
    {
      key: 'item',
      header: t('table.item'),
      cell: (r) => (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <I.Timeline size={12} />
          <span style={{ color: 'var(--gray-50)' }}>{r.intent || truncId(r.id)}</span>
        </span>
      ),
    },
    {
      key: 'status',
      header: t('table.status'),
      cell: (r) => (
        <Badge tone={r.status === 'needs_decision' ? 'rose' : 'blue'} dot>
          {r.status}
        </Badge>
      ),
    },
    {
      key: 'updated',
      header: t('table.updated'),
      cellClassName: 'whitespace-nowrap',
      cell: (r) => (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
          <span className="faded">{relTime(r.updated_at)}</span>
          {r.pr_number && r.pr_url && (
            <a
              href={r.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={stopAnchorPropagation}
              style={{ color: 'var(--blue-500)', textDecoration: 'none' }}
              title={r.pr_url}
            >
              PR #{r.pr_number}
            </a>
          )}
        </span>
      ),
    },
  ]
}

export function useDeliverableColumns(): ResponsiveTableColumn<BriefDeliverable>[] {
  const t = useTranslations('nexus.brief')
  return [
    {
      key: 'item',
      header: t('table.item'),
      cell: (d) => (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <I.Doc size={12} />
          <span style={{ color: 'var(--gray-50)' }}>{d.title}</span>
        </span>
      ),
    },
    {
      key: 'status',
      header: t('table.status'),
      cell: (d) => <ProofBadge state={d.proof_state} />,
    },
    {
      key: 'summary',
      header: t('table.summary'),
      cell: (d) => (
        <span className="mono faded" style={{ fontSize: 11 }} title={d.proof_summary ?? undefined}>
          {d.proof_summary ?? '—'}
        </span>
      ),
    },
    {
      key: 'updated',
      header: t('table.updated'),
      cellClassName: 'whitespace-nowrap',
      cell: (d) => (
        <span className="faded" style={{ fontSize: 11 }}>
          {relTime(d.created_at)}
        </span>
      ),
    },
  ]
}

export function useBlockedColumns(): ResponsiveTableColumn<BriefBlockedItem>[] {
  const t = useTranslations('nexus.brief')
  return [
    {
      key: 'item',
      header: t('table.item'),
      cell: (item) => (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <I.Doc size={12} />
          <span style={{ color: 'var(--gray-50)' }}>{item.title}</span>
        </span>
      ),
    },
    {
      key: 'status',
      header: t('table.status'),
      cell: (item) => <ProofBadge state={item.proof_state} />,
    },
    {
      key: 'updated',
      header: t('table.updated'),
      cellClassName: 'whitespace-nowrap',
      cell: (item) => (
        <span className="faded" style={{ fontSize: 11 }}>
          {relTime(item.created_at)}
        </span>
      ),
    },
  ]
}

export { DeliverableCard }
