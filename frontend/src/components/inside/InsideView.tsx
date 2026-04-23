import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { compositionSnapshotsApi, requestsApi } from '../../api/founder'
import type {
  CompositionSnapshot,
  ExecutionRun,
  Request as FounderRequest,
} from '../../types/founder'

interface InsideViewProps {
  projectId?: string
}

export default function InsideView({ projectId }: InsideViewProps) {
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null)
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string | null>(null)

  const { data: requests = [] } = useQuery<FounderRequest[]>({
    queryKey: ['requests', projectId],
    queryFn: () => requestsApi.listForProject(projectId!),
    enabled: Boolean(projectId),
  })

  if (!projectId) {
    return (
      <div className="flex h-full items-center justify-center bg-bg-primary text-sm text-text-tertiary">
        Select a project first.
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col bg-bg-primary">
      <header className="border-b border-border px-6 py-4">
        <h2 className="text-lg font-semibold text-text-primary">Inside</h2>
        <p className="text-sm text-text-secondary">
          How the company processed a request. Runs, composition, and which services touched it.
        </p>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <RequestTreePanel
          requests={requests}
          selectedRequestId={selectedRequestId}
          onSelectRequest={(id) => {
            setSelectedRequestId(id)
            setSelectedSnapshotId(null)
          }}
          onSelectSnapshot={setSelectedSnapshotId}
        />
        <SnapshotDetailPanel snapshotId={selectedSnapshotId} />
      </div>
    </div>
  )
}

function RequestTreePanel({
  requests,
  selectedRequestId,
  onSelectRequest,
  onSelectSnapshot,
}: {
  requests: FounderRequest[]
  selectedRequestId: string | null
  onSelectRequest: (id: string) => void
  onSelectSnapshot: (id: string) => void
}) {
  return (
    <section className="w-80 overflow-y-auto border-r border-border bg-bg-surface p-4">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-tertiary">
        Requests
      </h3>
      {requests.length === 0 ? (
        <p className="text-xs text-text-tertiary">No requests yet.</p>
      ) : (
        <ul className="space-y-1">
          {requests.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onSelectRequest(r.id)}
                className={`w-full rounded px-2 py-1.5 text-left text-xs transition-colors ${
                  selectedRequestId === r.id
                    ? 'bg-accent text-bg-primary'
                    : 'text-text-secondary hover:bg-bg-hover'
                }`}
              >
                {r.intent_summary}
              </button>
              {selectedRequestId === r.id && (
                <RunList requestId={r.id} onSelectSnapshot={onSelectSnapshot} />
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function RunList({
  requestId,
  onSelectSnapshot,
}: {
  requestId: string
  onSelectSnapshot: (id: string) => void
}) {
  const { data: runs = [], isLoading } = useQuery<ExecutionRun[]>({
    queryKey: ['runs', requestId],
    queryFn: () => requestsApi.listRuns(requestId),
  })

  if (isLoading) {
    return <p className="mt-1 pl-3 text-[10px] text-text-tertiary">Loading…</p>
  }
  if (runs.length === 0) {
    return <p className="mt-1 pl-3 text-[10px] text-text-tertiary">No runs yet.</p>
  }
  return (
    <ul className="mt-1 space-y-0.5 pl-3">
      {runs.map((run) => (
        <li key={run.id}>
          <button
            type="button"
            disabled={!run.composition_snapshot_id}
            onClick={() =>
              run.composition_snapshot_id && onSelectSnapshot(run.composition_snapshot_id)
            }
            className="block w-full truncate rounded px-2 py-1 text-left text-[11px] font-mono text-text-tertiary hover:bg-bg-hover disabled:opacity-50"
            title={run.id}
          >
            {run.status} · {run.id.slice(0, 8)}
          </button>
        </li>
      ))}
    </ul>
  )
}

function SnapshotDetailPanel({ snapshotId }: { snapshotId: string | null }) {
  const { data, isLoading } = useQuery<CompositionSnapshot>({
    queryKey: ['composition-snapshot', snapshotId],
    queryFn: () => compositionSnapshotsApi.get(snapshotId!),
    enabled: Boolean(snapshotId),
  })

  if (!snapshotId) {
    return (
      <section className="flex flex-1 items-center justify-center px-6 py-4">
        <p className="text-sm text-text-tertiary">
          Pick a run to see its composition snapshot.
        </p>
      </section>
    )
  }

  if (isLoading || !data) {
    return (
      <section className="flex flex-1 items-center justify-center px-6 py-4">
        <p className="text-sm text-text-tertiary">Loading…</p>
      </section>
    )
  }

  const inline = (data.system_prompt_ref as Record<string, unknown>).inline as string | undefined

  return (
    <section className="flex-1 overflow-y-auto px-6 py-4">
      <div className="mx-auto max-w-3xl space-y-4">
        <div className="rounded-lg border border-border bg-bg-card p-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-text-primary">{data.persona_label}</span>
            <span
              className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider ${
                data.source === 'bsage' ? 'border-brand-emerald text-brand-emerald' : 'border-border text-text-tertiary'
              }`}
            >
              {data.source}
            </span>
          </div>
          {data.fit_score !== null && (
            <p className="text-xs text-text-tertiary">fit score: {data.fit_score.toFixed(2)}</p>
          )}
          <p className="mt-1 text-xs text-text-tertiary">tools: {data.tools_allowed.join(', ') || 'none'}</p>
        </div>

        <div className="rounded-lg border border-border bg-bg-card p-4">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-tertiary">
            System prompt
          </h4>
          <pre className="whitespace-pre-wrap font-mono text-xs text-text-secondary">
            {inline ?? JSON.stringify(data.system_prompt_ref, null, 2)}
          </pre>
        </div>

        {data.context_doc_refs.length > 0 && (
          <div className="rounded-lg border border-border bg-bg-card p-4">
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-tertiary">
              Context ({data.context_doc_refs.length})
            </h4>
            <ul className="space-y-1 text-xs text-text-secondary">
              {data.context_doc_refs.map((ref) => (
                <li key={ref.excerpt_hash}>
                  <span className="font-mono text-text-tertiary">{ref.excerpt_hash.slice(0, 8)}</span>{' '}
                  {ref.title} <span className="text-text-tertiary">({ref.score.toFixed(2)})</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  )
}
