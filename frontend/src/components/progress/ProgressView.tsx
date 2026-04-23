import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { Badge } from '../common/Badge'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import { statusTone, accentHex, type Tone } from '../../lib/tone'
import { deliverablesApi } from '../../api/founder'
import type { Deliverable, DeliverableType } from '../../types/founder'

const TYPE_FILTERS = ['all', 'code', 'doc', 'design', 'data', 'url'] as const
type TypeFilter = (typeof TYPE_FILTERS)[number]

/**
 * Progress — single-column timeline. Sibling service status lives in
 * the topbar pills already, so there are no "trust cards" here.
 */
export default function ProgressView({ projectId }: { projectId: string }) {
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')

  const { data: deliverables = [] } = useQuery<Deliverable[]>({
    queryKey: ['deliverables', projectId],
    queryFn: () => deliverablesApi.listForProject(projectId),
  })

  const sorted = useMemo(
    () =>
      [...deliverables].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [deliverables],
  )
  const filtered = useMemo(() => {
    if (typeFilter === 'all') return sorted
    return sorted.filter((d) => d.type === typeFilter)
  }, [sorted, typeFilter])

  return (
    <div style={{ overflow: 'auto', height: '100%', padding: '24px 32px' }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 20 }}>
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
              className={`btn btn-sm ${typeFilter === t ? 'btn-secondary' : 'btn-ghost'}`}
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
              Start a request in the chat.
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
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => navigator.clipboard.writeText(d.id)}
            title="Copy deliverable id"
          >
            <I.Copy size={12} />
          </button>
        </div>
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
