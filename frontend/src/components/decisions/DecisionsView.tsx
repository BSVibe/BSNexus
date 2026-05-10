import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge, StatusDot } from '../common/Badge'
import { relTime, truncId } from '../../lib/fmt'
import { type Tone } from '../../lib/tone'
import { decisionsApi } from '../../api/founder'
import type { Decision } from '../../types/founder'

export default function DecisionsView({ projectId }: { projectId: string }) {
  const t = useTranslations('nexus.decisions')
  const queryClient = useQueryClient()

  const { data: decisions = [], isLoading } = useQuery<Decision[]>({
    queryKey: ['decisions', projectId],
    queryFn: () => decisionsApi.listForProject(projectId),
  })

  const resolveMutation = useMutation({
    mutationFn: ({ id, resolution }: { id: string; resolution: string }) =>
      decisionsApi.resolve(id, { resolution }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['decisions', projectId] })
    },
  })

  const blocking = decisions.filter((d) => !d.resolved_at && d.blocking)
  const open = decisions.filter((d) => !d.resolved_at && !d.blocking)
  const resolved = decisions.filter((d) => d.resolved_at)

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
              {blocking.length === 0 && <EmptyInbox label={t('nothingBlocking')} />}
              {blocking.map((d) => (
                <DecisionCard
                  key={d.id}
                  d={d}
                  pending={resolveMutation.isPending}
                  onResolve={(resolution) =>
                    resolveMutation.mutate({ id: d.id, resolution })
                  }
                />
              ))}
            </Section>
            {open.length > 0 && (
              <Section title={t('section.open')} count={open.length}>
                {open.map((d) => (
                  <DecisionCard
                    key={d.id}
                    d={d}
                    pending={resolveMutation.isPending}
                    onResolve={(resolution) =>
                      resolveMutation.mutate({ id: d.id, resolution })
                    }
                  />
                ))}
              </Section>
            )}
            {resolved.length > 0 && (
              <Section title={t('section.resolved')} count={resolved.length}>
                {resolved.map((d) => (
                  <DecisionCard key={d.id} d={d} onResolve={() => undefined} />
                ))}
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
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>{children}</div>
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

function DecisionCard({
  d,
  onResolve,
  pending,
}: {
  d: Decision
  onResolve: (resolution: string) => void
  pending?: boolean
}) {
  const t = useTranslations('nexus.decisions')
  const [custom, setCustom] = useState('')
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
            {d.options.map((opt) => (
              <button
                key={opt}
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={pending}
                onClick={() => onResolve(opt)}
              >
                {opt}
              </button>
            ))}
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (custom.trim()) onResolve(custom.trim())
            }}
            style={{ display: 'flex', gap: 8, alignItems: 'center' }}
          >
            <input
              className="input"
              placeholder={t('customPlaceholder')}
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
            />
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              disabled={!custom.trim() || pending}
            >
              {t('resolveButton')}
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
