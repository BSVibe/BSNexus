import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useQuery } from '@tanstack/react-query'

import { I } from '../../lib/icons'
import { truncId } from '../../lib/fmt'
import { statusTone } from '../../lib/tone'
import { Badge, StatusDot } from '../common/Badge'
import { requestsApi, compositionSnapshotsApi } from '../../api/founder'
import type {
  CompositionSnapshot,
  ExecutionRun,
  Request as FounderRequest,
} from '../../types/founder'

interface InspectorProps {
  projectId: string
  focusRequestId?: string | null
}

export default function Inspector({ projectId, focusRequestId }: InspectorProps) {
  const t = useTranslations('nexus.inside')
  // Track the user's explicit pick; the parent's focusRequestId and
  // the request list's first entry act as fallbacks, derived during
  // render to avoid setState-in-effect cascades.
  const [manualRequestId, setManualRequestId] = useState<string | null>(null)
  const [selectedRun, setSelectedRun] = useState<ExecutionRun | null>(null)

  const { data: requests = [] } = useQuery<FounderRequest[]>({
    queryKey: ['requests', projectId],
    queryFn: () => requestsApi.listForProject(projectId),
    refetchInterval: 3000,
  })

  const selectedRequest =
    (manualRequestId && requests.some((r) => r.id === manualRequestId)
      ? manualRequestId
      : null) ??
    (focusRequestId && requests.some((r) => r.id === focusRequestId)
      ? focusRequestId
      : null) ??
    requests[0]?.id ??
    null
  const setSelectedRequest = setManualRequestId

  return (
    <div
      className="project-inspector"
      style={{
        height: '100%',
        display: 'grid',
        minHeight: 0,
      }}
    >
          <div
            className="project-inspector__requests"
            style={{
              borderRight: '1px solid var(--border-subtle)',
              overflow: 'auto',
              padding: '8px 4px',
            }}
          >
            <div
              style={{
                padding: '4px 8px',
                fontSize: 10,
                color: 'var(--text-tertiary)',
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
              }}
            >
              {t('requestsHeading')}
            </div>
            {requests.length === 0 && (
              <div className="faded" style={{ fontSize: 11, padding: '4px 8px' }}>
                {t('noRequests')}
              </div>
            )}
            {requests.map((r) => (
              <div key={r.id}>
                <button
                  type="button"
                  className={`sb-item ${r.id === selectedRequest ? 'active' : ''}`}
                  style={{ margin: '2px 4px', width: 'calc(100% - 8px)' }}
                  onClick={() => {
                    setSelectedRequest(r.id)
                    setSelectedRun(null)
                  }}
                >
                  <StatusDot tone={statusTone(r.status)} size={6} />
                  <span className="label" style={{ fontSize: 12 }}>
                    {r.intent_summary}
                  </span>
                </button>
                {r.id === selectedRequest && (
                  <RunList
                    requestId={r.id}
                    selectedRunId={selectedRun?.id ?? null}
                    onSelectRun={setSelectedRun}
                  />
                )}
              </div>
            ))}
          </div>

          <div className="project-inspector__detail" style={{ overflow: 'auto', padding: 16 }}>
            {!selectedRun && (
              <div
                style={{
                  textAlign: 'center',
                  color: 'var(--text-tertiary)',
                  padding: 48,
                  fontSize: 13,
                }}
              >
                {t('pickRun')}
              </div>
            )}
            {selectedRun && <RunDetail run={selectedRun} />}
          </div>
    </div>
  )
}

function RunList({
  requestId,
  selectedRunId,
  onSelectRun,
}: {
  requestId: string
  selectedRunId: string | null
  onSelectRun: (run: ExecutionRun) => void
}) {
  const t = useTranslations('nexus.inside')
  const { data: runs = [], isLoading } = useQuery<ExecutionRun[]>({
    queryKey: ['runs', requestId],
    queryFn: () => requestsApi.listRuns(requestId),
    refetchInterval: 3000,
  })

  useEffect(() => {
    if (!selectedRunId && runs.length > 0) onSelectRun(runs[0])
  }, [runs, selectedRunId, onSelectRun])

  if (isLoading) {
    return (
      <div style={{ padding: '4px 8px 8px 16px' }}>
        <span className="faded" style={{ fontSize: 11 }}>
          {t('loading')}
        </span>
      </div>
    )
  }
  if (runs.length === 0) {
    return (
      <div style={{ padding: '4px 8px 8px 16px' }}>
        <span className="faded" style={{ fontSize: 11 }}>
          {t('noRunsYet')}
        </span>
      </div>
    )
  }
  return (
    <div style={{ padding: '4px 8px 8px 16px' }}>
      {runs.map((run) => (
        <button
          key={run.id}
          type="button"
          className={`sb-item ${run.id === selectedRunId ? 'active' : ''}`}
          style={{
            margin: '2px 0',
            paddingLeft: run.parent_run_id ? 20 : 8,
          }}
          onClick={() => onSelectRun(run)}
          title={run.id}
        >
          <StatusDot tone={statusTone(run.status)} size={6} />
          <span className="label" style={{ fontSize: 12, fontFamily: 'var(--font-mono)' }}>
            {run.status} · {truncId(run.id)}
          </span>
        </button>
      ))}
    </div>
  )
}

// Status labels we provide localized strings for; statuses outside this
// set fall through as raw values so newly-introduced backend statuses
// stay visible during a hot deploy.
const KNOWN_RUN_STATUSES = new Set([
  'pending',
  'running',
  'blocked',
  'done',
])

function RunDetail({ run }: { run: ExecutionRun }) {
  const t = useTranslations('nexus.inside')
  const tCommon = useTranslations('nexus.common')
  const tStatus = useTranslations('nexus.status')
  const snapshotId = run.composition_snapshot_id
  const { data: snapshot, isLoading } = useQuery<CompositionSnapshot>({
    queryKey: ['composition-snapshot', snapshotId],
    queryFn: () => compositionSnapshotsApi.get(snapshotId!),
    enabled: Boolean(snapshotId),
  })

  if (!snapshotId) {
    return (
      <div
        style={{
          textAlign: 'center',
          color: 'var(--text-tertiary)',
          padding: 32,
          fontSize: 13,
        }}
      >
        {t('noSnapshot')}
      </div>
    )
  }
  if (isLoading || !snapshot) {
    return (
      <div style={{ padding: 16 }}>
        <div className="skel" style={{ height: 12, marginBottom: 8 }} />
        <div className="skel" style={{ height: 60 }} />
      </div>
    )
  }
  const inline = (snapshot.system_prompt_ref as Record<string, unknown>)?.inline as
    | string
    | undefined
  const output = extractInline(run.output_ref)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 6,
          }}
        >
          <Badge tone={snapshot.source === 'bsage' ? 'emerald' : 'gray'}>
            {snapshot.source === 'bsage' ? t('source.bsage') : t('source.local')}
          </Badge>
          <span className="mono faded" style={{ fontSize: 11 }}>
            {truncId(snapshot.id)}
          </span>
          {snapshot.fit_score !== null && (
            <>
              <span style={{ flex: 1 }} />
              <span className="mono faded" style={{ fontSize: 11 }}>
                {t('fitPrefix')} {snapshot.fit_score.toFixed(2)}
              </span>
            </>
          )}
        </div>
        <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--gray-50)' }}>
          {snapshot.persona_label}
        </div>
      </div>

      <DetailRow label={t('row.toolsAllowed')}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {snapshot.tools_allowed.length === 0 && (
            <span className="faded" style={{ fontSize: 11 }}>
              {t('row.toolsAllowedNone')}
            </span>
          )}
          {snapshot.tools_allowed.map((t) => (
            <span
              key={t}
              className="mono"
              style={{
                fontSize: 11,
                padding: '2px 8px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--r-sm)',
                color: 'var(--gray-300)',
              }}
            >
              {t}
            </span>
          ))}
        </div>
      </DetailRow>

      <DetailRow
        label={t('row.systemPrompt')}
        actions={
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => inline && navigator.clipboard.writeText(inline)}
          >
            <I.Copy size={12} /> {tCommon('copy')}
          </button>
        }
      >
        <pre
          style={{
            margin: 0,
            padding: 12,
            background: 'var(--bg-base)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--r-md)',
            fontSize: 12,
            lineHeight: '18px',
            color: 'var(--gray-200)',
            whiteSpace: 'pre-wrap',
            maxHeight: 260,
            overflow: 'auto',
          }}
        >
          {inline ?? JSON.stringify(snapshot.system_prompt_ref, null, 2)}
        </pre>
      </DetailRow>

      {output && (
        <DetailRow
          label={t('row.output')}
          actions={
            <>
              <Badge tone={run.status === 'done' ? 'emerald' : 'gray'}>
                {KNOWN_RUN_STATUSES.has(run.status) ? tStatus(run.status as never) : run.status}
              </Badge>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => navigator.clipboard.writeText(output)}
              >
                <I.Copy size={12} /> {tCommon('copy')}
              </button>
            </>
          }
        >
          <pre
            style={{
              margin: 0,
              padding: 12,
              background: 'var(--bg-base)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--r-md)',
              fontSize: 12,
              lineHeight: '18px',
              color: 'var(--gray-100)',
              whiteSpace: 'pre-wrap',
              maxHeight: 360,
              overflow: 'auto',
            }}
          >
            {output}
          </pre>
        </DetailRow>
      )}

      {snapshot.context_doc_refs.length > 0 && (
        <DetailRow
          label={t('row.contextDocs')}
          actions={
            <span className="mono faded" style={{ fontSize: 11 }}>
              {snapshot.context_doc_refs.length} {t('row.refsSuffix')}
            </span>
          }
        >
          {snapshot.context_doc_refs.map((c) => (
            <div
              key={c.excerpt_hash}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '6px 0',
                borderBottom: '1px solid var(--border-subtle)',
              }}
            >
              <I.Doc size={12} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    color: 'var(--gray-100)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {c.title}
                </div>
                <div className="mono faded" style={{ fontSize: 11 }}>
                  {c.path}
                </div>
              </div>
              <span className="mono faded" style={{ fontSize: 11 }}>
                {c.score.toFixed(2)}
              </span>
            </div>
          ))}
        </DetailRow>
      )}
    </div>
  )
}

function extractInline(ref: Record<string, unknown> | null): string | null {
  if (!ref) return null
  const v = ref.inline
  if (typeof v === 'string' && v.trim().length > 0) return v
  return null
}

function DetailRow({
  label,
  actions,
  children,
}: {
  label: string
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginBottom: 6,
        }}
      >
        <div
          style={{
            fontSize: 10,
            color: 'var(--text-tertiary)',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}
        >
          {label}
        </div>
        <span style={{ flex: 1 }} />
        {actions}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>{children}</div>
    </div>
  )
}
