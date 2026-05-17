import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ResponsiveTable } from '@bsvibe/ui'
import type { ResponsiveTableColumn } from '@bsvibe/ui'

import { Badge, StatusDot } from '../common/Badge'
import { relTime, truncId } from '../../lib/fmt'
import { type Tone } from '../../lib/tone'
import { decisionsApi } from '../../api/founder'
import type { Decision, DecisionResolve } from '../../types/founder'

export default function DecisionsView({ projectId }: { projectId: string }) {
  const t = useTranslations('nexus.decisions')
  const queryClient = useQueryClient()

  const { data: decisions = [], isLoading } = useQuery<Decision[]>({
    queryKey: ['decisions', projectId],
    queryFn: () => decisionsApi.listForProject(projectId),
  })

  const resolveMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: DecisionResolve }) =>
      decisionsApi.resolve(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['decisions', projectId] })
    },
  })

  const blocking = decisions.filter((d) => !d.resolved_at && d.blocking)
  const open = decisions.filter((d) => !d.resolved_at && !d.blocking)
  const resolved = decisions.filter((d) => d.resolved_at)

  function onResolve(id: string, payload: DecisionResolve) {
    resolveMutation.mutate({ id, payload })
  }

  return (
    <div className="decisions-view" style={{ overflow: 'auto', height: '100%' }}>
      <div className="decisions-view__inner" style={{ maxWidth: 820, margin: '0 auto' }}>
        {isLoading ? (
          <p className="faded" style={{ fontSize: 13 }}>
            {t('loading')}
          </p>
        ) : decisions.length === 0 ? (
          <EmptyInbox label={t('inboxClear')} />
        ) : (
          <>
            <Section title={t('section.blocking')} count={blocking.length} tone="rose">
              <DecisionTable
                decisions={blocking}
                emptyMessage={t('nothingBlocking')}
                pending={resolveMutation.isPending}
                onResolve={onResolve}
              />
            </Section>
            {open.length > 0 && (
              <Section title={t('section.open')} count={open.length}>
                <DecisionTable
                  decisions={open}
                  emptyMessage={t('inboxClear')}
                  pending={resolveMutation.isPending}
                  onResolve={onResolve}
                />
              </Section>
            )}
            {resolved.length > 0 && (
              <Section title={t('section.resolved')} count={resolved.length}>
                <DecisionTable
                  decisions={resolved}
                  emptyMessage={t('inboxClear')}
                  onResolve={() => undefined}
                />
              </Section>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Section({
  title,
  count,
  tone = 'gray',
  children,
}: {
  title: string
  count: number
  tone?: Tone
  children: React.ReactNode
}) {
  return (
    <section style={{ marginBottom: 32 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <StatusDot tone={tone} />
        <h3
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--gray-100)',
            margin: 0,
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}
        >
          {title}
        </h3>
        <span className="mono faded" style={{ fontSize: 11 }}>
          {count}
        </span>
      </div>
      {children}
    </section>
  )
}

function EmptyInbox({ label }: { label: string }) {
  return (
    <div
      className="card"
      style={{
        padding: 32,
        textAlign: 'center',
        fontSize: 13,
        color: 'var(--text-tertiary)',
      }}
    >
      {label}
    </div>
  )
}

/**
 * DecisionTable — the per-section list rendered through the shared
 * `<ResponsiveTable>`. Desktop gets a real `<table>` with one row per
 * decision; mobile keeps the rich `<DecisionCard>` via `renderMobileCard`
 * so the resolve form's vertical layout isn't squeezed into table cells.
 */
function DecisionTable({
  decisions,
  emptyMessage,
  pending,
  onResolve,
}: {
  decisions: Decision[]
  emptyMessage: string
  pending?: boolean
  onResolve: (id: string, payload: DecisionResolve) => void
}) {
  const t = useTranslations('nexus.decisions')

  const columns: ResponsiveTableColumn<Decision>[] = [
    {
      key: 'question',
      header: t('table.question'),
      cell: (d) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ color: 'var(--gray-50)', fontWeight: 500 }}>{d.question}</span>
          <span className="mono faded" style={{ fontSize: 11 }}>
            {truncId(d.id)}
            {d.request_id ? ` · ${truncId(d.request_id)}` : ''}
          </span>
        </div>
      ),
    },
    {
      key: 'status',
      header: t('table.status'),
      cell: (d) =>
        d.resolved_at ? (
          <Badge tone="emerald" dot>
            {t('resolvedBadge')}
          </Badge>
        ) : d.blocking ? (
          <Badge tone="rose" dot>
            {t('blockingBadge')}
          </Badge>
        ) : (
          <span className="faded" style={{ fontSize: 12 }}>
            {t('section.open')}
          </span>
        ),
    },
    {
      key: 'raised',
      header: t('table.raised'),
      cellClassName: 'whitespace-nowrap',
      cell: (d) => (
        <span className="faded" style={{ fontSize: 11 }}>
          {relTime(d.created_at)}
        </span>
      ),
    },
    {
      key: 'action',
      header: t('table.action'),
      cell: (d) => <ResolveCell d={d} pending={pending} onResolve={onResolve} />,
    },
  ]

  return (
    <ResponsiveTable
      columns={columns}
      rows={decisions}
      rowKey={(d) => d.id}
      emptyMessage={emptyMessage}
      renderMobileCard={(d) => (
        <DecisionCard
          d={d}
          pending={pending}
          onResolve={(payload) => onResolve(d.id, payload)}
        />
      )}
    />
  )
}

/**
 * ResolveCell — compact desktop resolve UI. The two outcomes are
 * forward-only: a plain `Retry` button re-dispatches the work as-is, and
 * a `Reframe` form re-dispatches it with the founder's free-text
 * guidance. There is no `abandon` — every resolution moves work forward.
 */
function ResolveCell({
  d,
  pending,
  onResolve,
}: {
  d: Decision
  pending?: boolean
  onResolve: (id: string, payload: DecisionResolve) => void
}) {
  const t = useTranslations('nexus.decisions')
  const [guidance, setGuidance] = useState('')
  const isResolved = !!d.resolved_at

  if (isResolved) {
    return (
      <span style={{ fontSize: 12, color: 'var(--gray-200)' }}>
        {d.resolution}
        {d.resolved_by && (
          <span className="faded mono" style={{ fontSize: 11, marginLeft: 6 }}>
            · {t('byPrefix')} {d.resolved_by}
          </span>
        )}
      </span>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 200 }}>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        disabled={pending}
        onClick={() => onResolve(d.id, { resolution: 'retry' })}
        style={{ alignSelf: 'flex-start' }}
      >
        {t('retryLabel')}
      </button>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (guidance.trim()) {
            onResolve(d.id, { resolution: 'reframe', guidance: guidance.trim() })
          }
        }}
        style={{ display: 'flex', gap: 6, alignItems: 'center' }}
      >
        <input
          className="input"
          placeholder={t('reframePlaceholder')}
          value={guidance}
          onChange={(e) => setGuidance(e.target.value)}
        />
        <button
          type="submit"
          className="btn btn-primary btn-sm"
          disabled={!guidance.trim() || pending}
        >
          {t('reframeLabel')}
        </button>
      </form>
    </div>
  )
}

function DecisionCard({
  d,
  onResolve,
  pending,
}: {
  d: Decision
  onResolve: (payload: DecisionResolve) => void
  pending?: boolean
}) {
  const t = useTranslations('nexus.decisions')
  const [guidance, setGuidance] = useState('')
  const isResolved = !!d.resolved_at

  return (
    <div className="card" style={{ padding: 16, opacity: isResolved ? 0.65 : 1 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 12,
          marginBottom: 12,
        }}
      >
        <div style={{ flex: 1 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              marginBottom: 6,
            }}
          >
            {d.blocking && !isResolved && (
              <Badge tone="rose" dot>
                {t('blockingBadge')}
              </Badge>
            )}
            {isResolved && (
              <Badge tone="emerald" dot>
                {t('resolvedBadge')}
              </Badge>
            )}
            <span
              className="mono faded"
              style={{ fontSize: 11 }}
              title={d.id}
            >
              {truncId(d.id)}
            </span>
            {d.request_id && (
              <span className="mono faded" style={{ fontSize: 11 }}>
                · {truncId(d.request_id)}
              </span>
            )}
            <span style={{ flex: 1 }} />
            <span className="faded" style={{ fontSize: 11 }}>
              {relTime(d.created_at)}
            </span>
          </div>
          <div
            style={{
              fontSize: 15,
              color: 'var(--gray-50)',
              lineHeight: '22px',
              fontWeight: 500,
            }}
          >
            {d.question}
          </div>
        </div>
      </div>

      {!isResolved ? (
        <>
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 8,
              marginBottom: 8,
            }}
          >
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={pending}
              onClick={() => onResolve({ resolution: 'retry' })}
            >
              {t('retryLabel')}
            </button>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (guidance.trim()) {
                onResolve({ resolution: 'reframe', guidance: guidance.trim() })
              }
            }}
            style={{ display: 'flex', gap: 8, alignItems: 'center' }}
          >
            <input
              className="input"
              placeholder={t('reframePlaceholder')}
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
            />
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              disabled={!guidance.trim() || pending}
            >
              {t('reframeLabel')}
            </button>
          </form>
        </>
      ) : (
        <div
          style={{
            fontSize: 13,
            color: 'var(--gray-200)',
            padding: '8px 12px',
            background: 'var(--bg-elevated)',
            borderRadius: 'var(--r-md)',
            border: '1px solid var(--border-subtle)',
          }}
        >
          <span
            className="faded"
            style={{
              fontSize: 11,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              marginRight: 8,
            }}
          >
            {t('resolutionLabel')}
          </span>
          {d.resolution}
          {d.resolved_by && (
            <span
              className="faded mono"
              style={{ fontSize: 11, marginLeft: 8 }}
            >
              · {t('byPrefix')} {d.resolved_by}
            </span>
          )}
          {d.resolved_at && (
            <span className="faded" style={{ fontSize: 11, marginLeft: 8 }}>
              · {relTime(d.resolved_at)}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
