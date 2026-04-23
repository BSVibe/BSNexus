import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { decisionsApi } from '../../api/founder'
import type { Decision } from '../../types/founder'

interface DecisionsViewProps {
  projectId?: string
}

export default function DecisionsView({ projectId }: DecisionsViewProps) {
  if (!projectId) {
    return (
      <div className="flex h-full items-center justify-center bg-bg-primary text-sm text-text-tertiary">
        Select a project first.
      </div>
    )
  }

  return <DecisionsInner projectId={projectId} />
}

function DecisionsInner({ projectId }: { projectId: string }) {
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

  return (
    <div className="flex h-full flex-col bg-bg-primary">
      <header className="border-b border-border px-6 py-4">
        <h2 className="text-lg font-semibold text-text-primary">Decisions</h2>
        <p className="text-sm text-text-secondary">
          The company waits here when it needs you. Blocking items first.
        </p>
      </header>

      <section className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto max-w-3xl space-y-3">
          {isLoading ? (
            <p className="text-sm text-text-tertiary">Loading…</p>
          ) : decisions.length === 0 ? (
            <p className="mt-8 text-center text-sm text-text-tertiary">Inbox clear.</p>
          ) : (
            decisions.map((d) => (
              <DecisionCard
                key={d.id}
                decision={d}
                onResolve={(resolution) =>
                  resolveMutation.mutate({ id: d.id, resolution })
                }
                pending={resolveMutation.isPending}
              />
            ))
          )}
        </div>
      </section>
    </div>
  )
}

function DecisionCard({
  decision,
  onResolve,
  pending,
}: {
  decision: Decision
  onResolve: (resolution: string) => void
  pending: boolean
}) {
  const [draft, setDraft] = useState('')
  const resolved = decision.resolved_at !== null

  return (
    <article
      className={`rounded-lg border bg-bg-card p-4 ${
        decision.blocking && !resolved ? 'border-brand-rose' : 'border-border'
      }`}
    >
      <header className="mb-2 flex items-start justify-between gap-3">
        <p className="text-sm font-semibold text-text-primary">{decision.question}</p>
        <span className="shrink-0 rounded-full border border-current px-2 py-0.5 text-[10px] uppercase tracking-wider">
          {resolved ? 'resolved' : decision.blocking ? 'blocking' : 'fyi'}
        </span>
      </header>

      {decision.options.length > 0 && !resolved && (
        <ul className="mb-2 flex flex-wrap gap-1">
          {decision.options.map((opt) => (
            <li key={opt}>
              <button
                type="button"
                onClick={() => onResolve(opt)}
                disabled={pending}
                className="rounded border border-border px-2 py-0.5 text-xs text-text-secondary hover:border-accent"
              >
                {opt}
              </button>
            </li>
          ))}
        </ul>
      )}

      {resolved ? (
        <p className="text-xs text-text-tertiary">
          Resolved: <span className="text-text-secondary">{decision.resolution}</span>
        </p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (draft.trim()) onResolve(draft.trim())
          }}
          className="flex gap-2"
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Custom resolution"
            className="flex-1 rounded border border-border bg-bg-input px-2 py-1 text-sm text-text-primary"
          />
          <button
            type="submit"
            disabled={!draft.trim() || pending}
            className="rounded bg-accent px-3 py-1 text-xs font-semibold text-bg-primary disabled:opacity-40"
          >
            Resolve
          </button>
        </form>
      )}
    </article>
  )
}
