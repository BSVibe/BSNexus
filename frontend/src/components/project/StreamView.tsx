import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { Badge } from '../common/Badge'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import { statusTone, type Tone, accentHex } from '../../lib/tone'
import { conversationApi, type Message } from '../../api/conversation'
import { requestsApi, deliverablesApi, decisionsApi } from '../../api/founder'
import type {
  Decision,
  Deliverable,
  DeliverableType,
  Request as FounderRequest,
} from '../../types/founder'

type EventKind = 'message' | 'delivery' | 'decision-opened' | 'decision-resolved'
interface StreamEvent {
  kind: EventKind
  at: string
  data: Message | Deliverable | Decision
}

const FILTERS = ['all', 'messages', 'deliveries', 'decisions'] as const
type Filter = (typeof FILTERS)[number]

export default function StreamView({ projectId }: { projectId: string }) {
  const [filter, setFilter] = useState<Filter>('all')

  const { data: messages = [] } = useQuery<Message[]>({
    queryKey: ['messages', projectId],
    queryFn: () => conversationApi.list(projectId),
  })
  const { data: requests = [] } = useQuery<FounderRequest[]>({
    queryKey: ['requests', projectId],
    queryFn: () => requestsApi.listForProject(projectId),
  })
  const { data: deliverables = [] } = useQuery<Deliverable[]>({
    queryKey: ['deliverables', projectId],
    queryFn: () => deliverablesApi.listForProject(projectId),
  })
  const { data: decisions = [] } = useQuery<Decision[]>({
    queryKey: ['decisions', projectId],
    queryFn: () => decisionsApi.listForProject(projectId),
  })

  const events = useMemo<StreamEvent[]>(() => {
    const evs: StreamEvent[] = []
    messages.forEach((m) => evs.push({ kind: 'message', at: m.created_at, data: m }))
    deliverables.forEach((d) =>
      evs.push({ kind: 'delivery', at: d.created_at, data: d }),
    )
    decisions.forEach((d) => {
      evs.push({ kind: 'decision-opened', at: d.created_at, data: d })
      if (d.resolved_at) {
        evs.push({ kind: 'decision-resolved', at: d.resolved_at, data: d })
      }
    })
    return evs.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime())
  }, [messages, deliverables, decisions])

  const filtered = useMemo(() => {
    if (filter === 'all') return events
    if (filter === 'messages') return events.filter((e) => e.kind === 'message')
    if (filter === 'deliveries') return events.filter((e) => e.kind === 'delivery')
    return events.filter((e) => e.kind.startsWith('decision'))
  }, [events, filter])

  const openRequests = requests.filter(
    (r) => r.status === 'open' || r.status === 'running',
  )
  const openDecisions = decisions.filter((d) => !d.resolved_at)
  const recentDelivered = deliverables
    .filter((d) => d.status === 'delivered')
    .slice(0, 3)

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 300px',
        height: '100%',
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          borderRight: '1px solid var(--border-subtle)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '12px 32px',
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <h2
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--gray-100)',
              margin: 0,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
            }}
          >
            Project stream
          </h2>
          <span className="faded mono" style={{ fontSize: 11 }}>
            {filtered.length} events
          </span>
          <span style={{ flex: 1 }} />
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              className={`btn btn-sm ${filter === f ? 'btn-secondary' : 'btn-ghost'}`}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>

        <div
          style={{
            flex: 1,
            overflow: 'auto',
            padding: '24px 32px',
          }}
        >
          <div style={{ maxWidth: 780, margin: '0 auto' }}>
            <div
              style={{
                padding: '10px 14px',
                marginBottom: 20,
                border: '1px dashed var(--border-subtle)',
                borderRadius: 'var(--r-md)',
                fontSize: 12,
                color: 'var(--text-tertiary)',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <I.Sparkle size={12} style={{ color: 'var(--accent)' }} />
              This is a read view. To direct this project, talk to the company on the
              right and <span className="mono hl">@mention</span> it.
            </div>
            {filtered.length === 0 && (
              <div style={{ padding: '48px 0', textAlign: 'center' }}>
                <div style={{ fontSize: 15, fontWeight: 500, color: 'var(--gray-200)' }}>
                  Nothing here yet.
                </div>
                <div
                  style={{
                    fontSize: 12,
                    color: 'var(--text-tertiary)',
                    marginTop: 4,
                  }}
                >
                  Messages, deliveries, and decisions will show up as the company works.
                </div>
              </div>
            )}
            {filtered.map((e, i) => (
              <StreamEventItem key={i} e={e} />
            ))}
          </div>
        </div>
      </div>

      <aside
        style={{
          overflow: 'auto',
          padding: 16,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        <RightRailCard
          title="Active requests"
          count={openRequests.length}
          icon={<I.Zap size={14} />}
        >
          {openRequests.length === 0 && (
            <div className="faded" style={{ fontSize: 12, padding: '8px 0' }}>
              No active requests.
            </div>
          )}
          {openRequests.map((r) => (
            <div
              key={r.id}
              className="chip"
              style={{
                width: '100%',
                flexDirection: 'column',
                alignItems: 'stretch',
                padding: 10,
                gap: 8,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Badge tone={r.status === 'running' ? 'blue' : 'emerald'} dot>
                  {r.status}
                </Badge>
                <span className="mono faded" style={{ fontSize: 10 }}>
                  {truncId(r.id)}
                </span>
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--gray-200)',
                  textAlign: 'left',
                  lineHeight: '16px',
                }}
              >
                {r.intent_summary}
              </div>
              <div
                style={{
                  display: 'flex',
                  gap: 12,
                  fontSize: 11,
                  color: 'var(--text-tertiary)',
                }}
              >
                <span>{relTime(r.updated_at)}</span>
              </div>
            </div>
          ))}
        </RightRailCard>

        <RightRailCard
          title="Pending decisions"
          count={openDecisions.length}
          icon={<I.Alert size={14} />}
        >
          {openDecisions.length === 0 && (
            <div className="faded" style={{ fontSize: 12, padding: '8px 0' }}>
              Inbox clear.
            </div>
          )}
          {openDecisions.slice(0, 3).map((d) => (
            <div
              key={d.id}
              className="chip"
              style={{
                width: '100%',
                flexDirection: 'column',
                alignItems: 'stretch',
                padding: 10,
                gap: 6,
              }}
            >
              <div style={{ display: 'flex', gap: 6 }}>
                {d.blocking && (
                  <Badge tone="rose" dot>
                    blocking
                  </Badge>
                )}
                <span
                  className="mono faded"
                  style={{ fontSize: 10, marginLeft: 'auto' }}
                >
                  {truncId(d.id)}
                </span>
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--gray-200)',
                  lineHeight: '16px',
                  textAlign: 'left',
                }}
              >
                {d.question}
              </div>
            </div>
          ))}
        </RightRailCard>

        <RightRailCard
          title="Recent deliveries"
          count={recentDelivered.length}
          icon={<I.Check size={14} />}
        >
          {recentDelivered.length === 0 && (
            <div className="faded" style={{ fontSize: 12, padding: '8px 0' }}>
              Nothing delivered yet.
            </div>
          )}
          {recentDelivered.map((d) => (
            <div
              key={d.id}
              style={{
                padding: '8px 0',
                borderBottom: '1px solid var(--border-subtle)',
                fontSize: 12,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  marginBottom: 2,
                }}
              >
                <DeliverableTypeIcon t={d.type} />
                <span
                  style={{
                    color: 'var(--gray-200)',
                    flex: 1,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {d.title}
                </span>
              </div>
              <div className="faded" style={{ fontSize: 11 }}>
                {relTime(d.created_at)}
              </div>
            </div>
          ))}
        </RightRailCard>
      </aside>
    </div>
  )
}

function RightRailCard({
  title,
  count,
  icon,
  children,
}: {
  title: string
  count: number
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="card">
      <div className="card-hd" style={{ padding: '10px 12px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 11,
            fontWeight: 600,
            color: 'var(--gray-200)',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}
        >
          <span style={{ color: 'var(--text-tertiary)' }}>{icon}</span>
          {title}
        </div>
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
          {count}
        </span>
      </div>
      <div
        style={{
          padding: '8px 12px 12px',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        {children}
      </div>
    </div>
  )
}

function StreamEventItem({ e }: { e: StreamEvent }) {
  if (e.kind === 'message') {
    const m = e.data as Message
    const isUser = m.role === 'user'
    return (
      <div
        className="fade-in"
        style={{
          marginBottom: 20,
          display: 'flex',
          flexDirection: 'column',
          alignItems: isUser ? 'flex-end' : 'flex-start',
        }}
      >
        <div
          style={{
            display: 'flex',
            gap: 8,
            alignItems: 'flex-end',
            maxWidth: '82%',
            flexDirection: isUser ? 'row-reverse' : 'row',
          }}
        >
          <div
            style={{
              width: 24,
              height: 24,
              borderRadius: 6,
              background: isUser
                ? 'linear-gradient(135deg, #8b5cf6, var(--blue-500))'
                : 'linear-gradient(135deg,var(--blue-500),#1e40af)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 10,
              fontWeight: 700,
              color: '#fff',
              flex: 'none',
            }}
          >
            {isUser ? 'You' : 'BN'}
          </div>
          <div
            style={{
              padding: '10px 14px',
              borderRadius: 'var(--r-lg)',
              background: isUser ? 'rgba(59,130,246,0.08)' : 'var(--bg-surface)',
              border: isUser
                ? '1px solid rgba(59,130,246,0.3)'
                : '1px solid var(--border-subtle)',
              fontSize: 14,
              lineHeight: '22px',
              color: 'var(--gray-100)',
            }}
          >
            {m.content}
          </div>
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            marginTop: 4,
            padding: isUser ? '0 32px 0 0' : '0 0 0 32px',
            fontSize: 11,
            color: 'var(--text-tertiary)',
          }}
        >
          <span>{relTime(m.created_at)}</span>
          {m.request_id && (
            <>
              <span>·</span>
              <button
                type="button"
                className="mono"
                style={{
                  color: 'var(--text-tertiary)',
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  fontSize: 11,
                  padding: 0,
                }}
                onClick={() =>
                  document.dispatchEvent(
                    new CustomEvent('bsn:open-inspector', {
                      detail: { requestId: m.request_id },
                    }),
                  )
                }
                title={`Inspect ${m.request_id}`}
              >
                attached to {truncId(m.request_id)}
              </button>
            </>
          )}
        </div>
      </div>
    )
  }
  if (e.kind === 'delivery') {
    const d = e.data as Deliverable
    return (
      <div
        className="fade-in"
        style={{
          display: 'flex',
          gap: 10,
          alignItems: 'flex-start',
          margin: '0 0 20px',
        }}
      >
        <div
          style={{
            width: 24,
            height: 24,
            borderRadius: 6,
            background: 'rgba(16,185,129,0.1)',
            color: '#6ee7b7',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flex: 'none',
          }}
        >
          <I.Check size={12} />
        </div>
        <div
          style={{
            flex: 1,
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--r-md)',
            padding: '10px 12px',
            background: 'var(--bg-surface)',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 4,
            }}
          >
            <span
              className="faded"
              style={{
                fontSize: 11,
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
              }}
            >
              Delivered
            </span>
            <Badge tone={statusTone(d.status)} dot>
              {d.status}
            </Badge>
            <span style={{ flex: 1 }} />
            <span className="faded" style={{ fontSize: 11 }}>
              {relTime(d.created_at)}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <DeliverableTypeIcon t={d.type} />
            <span style={{ fontSize: 13, color: 'var(--gray-100)', fontWeight: 500 }}>
              {d.title}
            </span>
          </div>
        </div>
      </div>
    )
  }
  if (e.kind === 'decision-opened' || e.kind === 'decision-resolved') {
    const d = e.data as Decision
    const resolved = e.kind === 'decision-resolved'
    const tone: Tone = resolved ? 'emerald' : d.blocking ? 'rose' : 'amber'
    return (
      <div
        className="fade-in"
        style={{
          display: 'flex',
          gap: 10,
          alignItems: 'flex-start',
          margin: '0 0 20px',
        }}
      >
        <div
          style={{
            width: 24,
            height: 24,
            borderRadius: 6,
            background: `${accentHex[tone]}20`,
            color: accentHex[tone],
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flex: 'none',
          }}
        >
          {resolved ? <I.Check size={12} /> : <I.Alert size={12} />}
        </div>
        <div
          style={{
            flex: 1,
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--r-md)',
            padding: '10px 12px',
            background: 'var(--bg-surface)',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 4,
            }}
          >
            <span
              className="faded"
              style={{
                fontSize: 11,
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
              }}
            >
              {resolved ? 'Decision resolved' : 'Decision opened'}
            </span>
            {d.blocking && !resolved && (
              <Badge tone="rose" dot>
                blocking
              </Badge>
            )}
            <span style={{ flex: 1 }} />
            <span className="faded" style={{ fontSize: 11 }}>
              {relTime(e.at)}
            </span>
          </div>
          <div
            style={{
              fontSize: 13,
              color: 'var(--gray-100)',
              lineHeight: '18px',
            }}
          >
            {d.question}
          </div>
          {resolved && d.resolution && (
            <div style={{ fontSize: 12, color: '#6ee7b7', marginTop: 4 }}>
              → {d.resolution}
            </div>
          )}
        </div>
      </div>
    )
  }
  return null
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
