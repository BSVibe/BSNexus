import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { Badge } from '../common/Badge'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import { statusTone, accentHex, type Tone } from '../../lib/tone'
import { deliverablesApi } from '../../api/founder'
import {
  integrationsApi,
  type IntegrationConfigList,
} from '../../api/integrations'
import type {
  Deliverable,
  DeliverableType,
  IntegrationProvider,
} from '../../types/founder'

const TYPE_FILTERS = ['all', 'code', 'doc', 'design', 'data', 'url'] as const
type TypeFilter = (typeof TYPE_FILTERS)[number]

export default function ProgressView({ projectId }: { projectId: string }) {
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')

  const { data: deliverables = [] } = useQuery<Deliverable[]>({
    queryKey: ['deliverables', projectId],
    queryFn: () => deliverablesApi.listForProject(projectId),
  })

  const { data: integrations } = useQuery<IntegrationConfigList>({
    queryKey: ['integrations'],
    queryFn: integrationsApi.list,
  })

  const sorted = useMemo(
    () =>
      [...deliverables].sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [deliverables],
  )
  const filtered = useMemo(() => {
    if (typeFilter === 'all') return sorted
    return sorted.filter((d) => d.type === typeFilter)
  }, [sorted, typeFilter])

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 360px',
        height: '100%',
        minHeight: 0,
      }}
    >
      <div
        style={{
          overflow: 'auto',
          padding: '24px 32px',
          borderRight: '1px solid var(--border-subtle)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 20,
          }}
        >
          <h2
            style={{
              fontSize: 16,
              fontWeight: 600,
              color: 'var(--gray-100)',
              margin: 0,
            }}
          >
            Timeline
          </h2>
          <span className="faded mono" style={{ fontSize: 11 }}>
            {filtered.length} items
          </span>
          <span style={{ flex: 1 }} />
          {TYPE_FILTERS.map((t) => (
            <button
              key={t}
              type="button"
              className={`btn btn-sm ${
                typeFilter === t ? 'btn-secondary' : 'btn-ghost'
              }`}
              onClick={() => setTypeFilter(t)}
            >
              {t === 'all' ? 'All' : t}
            </button>
          ))}
        </div>

        {filtered.length === 0 && (
          <div className="card" style={{ padding: 48, textAlign: 'center' }}>
            <div style={{ fontSize: 14, color: 'var(--gray-200)', marginBottom: 4 }}>
              No deliverables yet.
            </div>
            <div className="faded" style={{ fontSize: 12 }}>
              Talk to the company on the right to kick something off.
            </div>
          </div>
        )}

        <div style={{ position: 'relative', paddingLeft: 24 }}>
          <div
            style={{
              position: 'absolute',
              left: 7,
              top: 0,
              bottom: 0,
              width: 1,
              background: 'var(--border-subtle)',
            }}
          />
          {filtered.map((d) => (
            <TimelineItem key={d.id} d={d} />
          ))}
        </div>
      </div>

      <aside
        style={{
          overflow: 'auto',
          padding: 16,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        <div
          style={{
            fontSize: 11,
            color: 'var(--text-tertiary)',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            padding: '0 4px',
          }}
        >
          Trust · sibling services
        </div>
        {(['bsage', 'bsgateway', 'bsupervisor'] as const).map((key) => (
          <TrustCard
            key={key}
            provider={key}
            enabled={integrations?.[key]?.enabled ?? false}
            baseUrl={integrations?.[key]?.base_url ?? null}
          />
        ))}
        <div
          style={{
            padding: '12px 12px',
            border: '1px dashed var(--border-subtle)',
            borderRadius: 'var(--r-md)',
            fontSize: 12,
            color: 'var(--text-tertiary)',
          }}
        >
          Missing a service?{' '}
          <a
            href="/settings"
            style={{ color: 'var(--accent)' }}
          >
            Configure in Settings → Integrations
          </a>
        </div>
      </aside>
    </div>
  )
}

function TimelineItem({ d }: { d: Deliverable }) {
  const tone: Tone = statusTone(d.status)
  return (
    <div style={{ position: 'relative', paddingBottom: 20 }}>
      <div
        style={{
          position: 'absolute',
          left: -20,
          top: 6,
          width: 14,
          height: 14,
          borderRadius: 99,
          background: 'var(--bg-base)',
          border: '2px solid var(--border-default)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <span
          style={{
            width: 4,
            height: 4,
            borderRadius: 99,
            background: accentHex[tone],
          }}
        />
      </div>
      <div className="card" style={{ padding: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <DeliverableTypeIcon t={d.type} />
          <span
            style={{
              fontSize: 13,
              fontWeight: 500,
              color: 'var(--gray-50)',
              flex: 1,
            }}
          >
            {d.title}
          </span>
          <Badge tone={tone} dot>
            {d.status}
          </Badge>
        </div>
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
          {d.request_id && (
            <>
              <span>·</span>
              <span className="mono" title={d.request_id}>
                {truncId(d.request_id)}
              </span>
            </>
          )}
          <span style={{ flex: 1 }} />
          <button type="button" className="btn btn-ghost btn-sm">
            <I.Copy size={12} />
          </button>
        </div>
      </div>
    </div>
  )
}

function TrustCard({
  provider,
  enabled,
  baseUrl,
}: {
  provider: IntegrationProvider
  enabled: boolean
  baseUrl: string | null
}) {
  const meta = {
    bsage: { label: 'BSage', sub: 'knowledge', accent: accentHex.emerald, Icon: I.Brain },
    bsgateway: {
      label: 'BSGateway',
      sub: 'routing',
      accent: accentHex.amber,
      Icon: I.Gateway,
    },
    bsupervisor: {
      label: 'BSupervisor',
      sub: 'audit',
      accent: accentHex.rose,
      Icon: I.Shield,
    },
  }[provider]
  const tone: Tone = !enabled ? 'gray' : 'emerald'
  return (
    <div
      className="card"
      style={{ padding: 14, borderLeft: `3px solid ${meta.accent}` }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <div
          style={{
            width: 28,
            height: 28,
            borderRadius: 'var(--r-md)',
            background: `${meta.accent}20`,
            color: meta.accent,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <meta.Icon size={14} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--gray-50)' }}>
            {meta.label}
          </div>
          <div className="mono faded" style={{ fontSize: 10 }}>
            {enabled && baseUrl ? baseUrl.replace(/^https?:\/\//, '') : meta.sub}
          </div>
        </div>
        <Badge tone={tone} dot>
          {enabled ? 'on' : 'off'}
        </Badge>
      </div>
    </div>
  )
}

function DeliverableTypeIcon({ t }: { t: DeliverableType }) {
  const icon: Record<DeliverableType, React.ReactNode> = {
    code: <I.Code size={12} />,
    doc: <I.Doc size={12} />,
    design: <I.Design size={12} />,
    data: <I.Data size={12} />,
    url: <I.Url size={12} />,
  }
  const color: Record<DeliverableType, string> = {
    code: '#93c5fd',
    doc: '#6ee7b7',
    design: '#fda4af',
    data: '#fcd34d',
    url: '#93c5fd',
  }
  return <span style={{ color: color[t] }}>{icon[t]}</span>
}
