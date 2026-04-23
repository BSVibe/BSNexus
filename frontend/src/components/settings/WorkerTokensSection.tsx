import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge, StatusDot } from '../common/Badge'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import {
  workersApi,
  type InstallTokenCreated,
  type InstallTokenStatus,
  type WorkerInfo,
} from '../../api/workers'

export default function WorkerTokensSection() {
  const queryClient = useQueryClient()
  const [revealedToken, setRevealedToken] = useState<string | null>(null)

  const { data: status, isLoading: statusLoading } = useQuery<InstallTokenStatus>({
    queryKey: ['install-token'],
    queryFn: workersApi.getInstallTokenStatus,
  })

  const { data: workers = [], isLoading: workersLoading } = useQuery<WorkerInfo[]>({
    queryKey: ['workers'],
    queryFn: workersApi.list,
    refetchInterval: 30_000, // status updates off heartbeat
  })

  const generateMutation = useMutation<InstallTokenCreated, Error, void>({
    mutationFn: () => workersApi.generateInstallToken(),
    onSuccess: (data) => {
      setRevealedToken(data.token)
      queryClient.invalidateQueries({ queryKey: ['install-token'] })
    },
  })

  const revokeMutation = useMutation({
    mutationFn: () => workersApi.revokeInstallToken(),
    onSuccess: () => {
      setRevealedToken(null)
      queryClient.invalidateQueries({ queryKey: ['install-token'] })
    },
  })

  const deleteWorkerMutation = useMutation({
    mutationFn: (id: string) => workersApi.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['workers'] }),
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <section className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <I.GitBranch size={14} />
          <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: 'var(--gray-50)' }}>
            Install token
          </h3>
          <span className="faded" style={{ fontSize: 11 }}>
            required for a remote worker to register
          </span>
        </div>

        {statusLoading ? (
          <p className="faded" style={{ fontSize: 13 }}>
            Loading…
          </p>
        ) : (
          <>
            {revealedToken && (
              <div
                style={{
                  padding: 12,
                  background: 'var(--bg-base)',
                  border: '1px solid rgba(245,158,11,0.3)',
                  borderRadius: 'var(--r-md)',
                  marginBottom: 12,
                }}
              >
                <div
                  style={{
                    fontSize: 10,
                    color: '#fcd34d',
                    textTransform: 'uppercase',
                    letterSpacing: '0.08em',
                    fontWeight: 600,
                    marginBottom: 4,
                  }}
                >
                  Copy now — shown only once
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <code
                    style={{
                      flex: 1,
                      fontSize: 12,
                      color: 'var(--gray-100)',
                      userSelect: 'all',
                      wordBreak: 'break-all',
                    }}
                  >
                    {revealedToken}
                  </code>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => navigator.clipboard.writeText(revealedToken)}
                  >
                    <I.Copy size={12} /> Copy
                  </button>
                </div>
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              {status?.has_token ? (
                <>
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 6,
                      fontSize: 12,
                      color: 'var(--text-secondary)',
                    }}
                  >
                    <StatusDot tone="emerald" size={6} /> Token configured
                  </span>
                  <span style={{ flex: 1 }} />
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => generateMutation.mutate()}
                    disabled={generateMutation.isPending}
                  >
                    {generateMutation.isPending ? 'Rotating…' : 'Regenerate'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => {
                      if (
                        confirm(
                          'Revoke the install token? Existing workers keep running; new workers cannot register.',
                        )
                      )
                        revokeMutation.mutate()
                    }}
                    disabled={revokeMutation.isPending}
                    style={{ color: '#fda4af' }}
                  >
                    Revoke
                  </button>
                </>
              ) : (
                <>
                  <span className="faded" style={{ fontSize: 12 }}>
                    No token configured — workers cannot register.
                  </span>
                  <span style={{ flex: 1 }} />
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => generateMutation.mutate()}
                    disabled={generateMutation.isPending}
                  >
                    {generateMutation.isPending ? 'Generating…' : 'Generate token'}
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </section>

      <section>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 8,
          }}
        >
          <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: 'var(--gray-50)' }}>
            Registered workers
          </h3>
          <span className="mono faded" style={{ fontSize: 11 }}>
            {workers.length}
          </span>
        </div>

        {workersLoading ? (
          <p className="faded" style={{ fontSize: 13 }}>
            Loading…
          </p>
        ) : workers.length === 0 ? (
          <div
            className="card"
            style={{
              padding: 24,
              textAlign: 'center',
              color: 'var(--text-tertiary)',
              fontSize: 13,
            }}
          >
            No workers yet. Run <code className="mono hl">bsnexus-worker register --token …</code>{' '}
            on a host after generating an install token above.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {workers.map((w) => (
              <WorkerRow
                key={w.id}
                worker={w}
                onDelete={() => {
                  if (confirm(`Remove worker "${w.name}"?`))
                    deleteWorkerMutation.mutate(w.id)
                }}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function WorkerRow({
  worker,
  onDelete,
}: {
  worker: WorkerInfo
  onDelete: () => void
}) {
  const tone: 'emerald' | 'amber' | 'gray' =
    worker.status === 'online'
      ? 'emerald'
      : worker.status === 'busy'
      ? 'amber'
      : 'gray'
  return (
    <div className="card" style={{ padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <StatusDot tone={tone} />
        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--gray-50)' }}>
          {worker.name}
        </span>
        <Badge tone={tone} square>
          {worker.status}
        </Badge>
        <span className="mono faded" style={{ fontSize: 11 }}>
          {truncId(worker.id)}
        </span>
        <span style={{ flex: 1 }} />
        {worker.last_heartbeat && (
          <span
            className="faded"
            style={{ fontSize: 11 }}
            title={new Date(worker.last_heartbeat).toLocaleString()}
          >
            heartbeat {relTime(worker.last_heartbeat)}
          </span>
        )}
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={onDelete}
          style={{ color: '#fda4af' }}
        >
          <I.X size={12} />
        </button>
      </div>
      {(worker.capabilities.length > 0 || worker.labels.length > 0) && (
        <div
          style={{
            marginTop: 8,
            display: 'flex',
            flexWrap: 'wrap',
            gap: 6,
          }}
        >
          {worker.capabilities.map((c) => (
            <span
              key={`cap-${c}`}
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
              {c}
            </span>
          ))}
          {worker.labels.map((l) => (
            <span
              key={`lbl-${l}`}
              className="mono"
              style={{
                fontSize: 11,
                padding: '2px 8px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--r-sm)',
                color: 'var(--text-secondary)',
              }}
            >
              {l}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
