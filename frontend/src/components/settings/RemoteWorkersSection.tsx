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

export default function RemoteWorkersSection() {
  const queryClient = useQueryClient()
  const [revealedToken, setRevealedToken] = useState<string | null>(null)

  const { data: status, isLoading: statusLoading } = useQuery<InstallTokenStatus>({
    queryKey: ['install-token'],
    queryFn: workersApi.getInstallTokenStatus,
  })

  const { data: workers = [], isLoading: workersLoading } = useQuery<WorkerInfo[]>({
    queryKey: ['workers'],
    queryFn: workersApi.list,
    refetchInterval: 30_000,
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <h3
          style={{
            margin: 0,
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--gray-50)',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          Remote workers
        </h3>
        <span className="mono faded" style={{ fontSize: 11 }}>
          {workers.length}
        </span>
        <span className="faded" style={{ fontSize: 12 }}>
          — hosts running <code className="mono hl">bsnexus-worker</code> that
          execute coding runs (claude-code · codex · opencode)
        </span>
      </div>

      <InstallTokenCard
        loading={statusLoading}
        hasToken={status?.has_token ?? false}
        revealedToken={revealedToken}
        onGenerate={() => generateMutation.mutate()}
        onRevoke={() => revokeMutation.mutate()}
        generating={generateMutation.isPending}
        revoking={revokeMutation.isPending}
      />

      <RegistrationGuide installToken={revealedToken} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div
          style={{
            fontSize: 11,
            color: 'var(--text-tertiary)',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}
        >
          Registered workers
        </div>
        {workersLoading ? (
          <p className="faded" style={{ fontSize: 13 }}>
            Loading…
          </p>
        ) : workers.length === 0 ? (
          <div
            className="card"
            style={{
              padding: 20,
              textAlign: 'center',
              color: 'var(--text-tertiary)',
              fontSize: 13,
            }}
          >
            No workers yet. Follow the guide above on any Linux/macOS host.
          </div>
        ) : (
          workers.map((w) => (
            <WorkerRow
              key={w.id}
              worker={w}
              onDelete={() => {
                if (confirm(`Remove worker "${w.name}"?`))
                  deleteWorkerMutation.mutate(w.id)
              }}
            />
          ))
        )}
      </div>
    </div>
  )
}

function InstallTokenCard({
  loading,
  hasToken,
  revealedToken,
  onGenerate,
  onRevoke,
  generating,
  revoking,
}: {
  loading: boolean
  hasToken: boolean
  revealedToken: string | null
  onGenerate: () => void
  onRevoke: () => void
  generating: boolean
  revoking: boolean
}) {
  return (
    <section className="card" style={{ padding: 14 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: revealedToken || hasToken ? 10 : 0,
        }}
      >
        <I.GitBranch size={14} />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--gray-50)' }}>
          Install token
        </span>
        <span className="faded" style={{ fontSize: 11 }}>
          one per tenant · used only during <code className="mono">register</code>
        </span>
        <span style={{ flex: 1 }} />
        {loading ? (
          <span className="faded" style={{ fontSize: 12 }}>
            Loading…
          </span>
        ) : hasToken ? (
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
              <StatusDot tone="emerald" size={6} /> configured
            </span>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={onGenerate}
              disabled={generating}
            >
              {generating ? 'Rotating…' : 'Regenerate'}
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
                  onRevoke()
              }}
              disabled={revoking}
              style={{ color: '#fda4af' }}
            >
              Revoke
            </button>
          </>
        ) : (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={onGenerate}
            disabled={generating}
          >
            {generating ? 'Generating…' : 'Generate token'}
          </button>
        )}
      </div>

      {revealedToken && (
        <div
          style={{
            padding: 10,
            background: 'var(--bg-base)',
            border: '1px solid rgba(245,158,11,0.3)',
            borderRadius: 'var(--r-md)',
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

      {!revealedToken && hasToken && (
        <div className="faded" style={{ fontSize: 11, marginTop: -4 }}>
          Raw token is only visible once, immediately after generation. Rotate
          to mint a new one — old value is discarded.
        </div>
      )}
    </section>
  )
}

function RegistrationGuide({ installToken }: { installToken: string | null }) {
  const origin =
    typeof window !== 'undefined' ? window.location.origin : 'https://nexus.bsvibe.dev'
  const token = installToken ?? '<INSTALL_TOKEN>'

  return (
    <section className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <I.Doc size={14} />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--gray-50)' }}>
          Registration guide
        </span>
        <span className="faded" style={{ fontSize: 11 }}>
          runs on Linux or macOS · needs Python 3.11+
        </span>
      </div>

      <Step
        n={1}
        title="Prerequisites"
        body={
          <ul
            style={{
              margin: '4px 0 0 16px',
              padding: 0,
              fontSize: 12,
              color: 'var(--text-secondary)',
              lineHeight: '20px',
            }}
          >
            <li>Python 3.11 or newer</li>
            <li>
              At least one coding CLI — <code className="mono hl">claude</code>,{' '}
              <code className="mono hl">codex</code>, or{' '}
              <code className="mono hl">opencode</code> (auto-detected)
            </li>
          </ul>
        }
      />

      <Step
        n={2}
        title="Install the worker"
        body={
          <CodeBlock text={`curl -fsSL ${origin}/worker/install.sh | bash`} />
        }
        hint={
          <>
            Drops <code className="mono">bsnexus-worker</code> into{' '}
            <code className="mono">~/.bsnexus-worker/</code> and adds it to your
            PATH.
          </>
        }
      />

      <Step
        n={3}
        title="Register against this tenant"
        body={
          <CodeBlock
            text={[
              'bsnexus-worker register \\',
              `  --server ${origin} \\`,
              '  --name "$(hostname)" \\',
              `  --token ${token}`,
            ].join('\n')}
          />
        }
        hint={
          <>
            Exchanges the install token for a long-lived worker token stored in{' '}
            <code className="mono">~/.bsnexus-worker/.env</code>. Add{' '}
            <code className="mono">--project &lt;id&gt;</code> to bind the worker
            to a single project.
          </>
        }
      />

      <Step
        n={4}
        title="Run"
        body={<CodeBlock text="cd /path/to/your/project\nbsnexus-worker run" />}
        hint={
          <>
            Polls <code className="mono">/api/v1/workers/poll</code> every 5s
            and heartbeats every 30s. The host appears below within a few
            seconds of the first successful heartbeat.
          </>
        }
      />

      {!installToken && (
        <div
          style={{
            marginTop: 10,
            padding: '8px 10px',
            fontSize: 11,
            color: 'var(--text-tertiary)',
            background: 'var(--bg-base)',
            border: '1px dashed var(--border-subtle)',
            borderRadius: 'var(--r-sm)',
          }}
        >
          Generate an install token above — the command in step 3 auto-updates
          with the real value while it's on screen.
        </div>
      )}
    </section>
  )
}

function Step({
  n,
  title,
  body,
  hint,
}: {
  n: number
  title: string
  body: React.ReactNode
  hint?: React.ReactNode
}) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '22px 1fr',
        gap: 10,
        padding: '8px 0',
        borderTop: n === 1 ? '1px solid var(--border-subtle)' : undefined,
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      <div
        style={{
          width: 22,
          height: 22,
          borderRadius: 99,
          background: 'var(--bg-elevated)',
          color: 'var(--gray-200)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 11,
          fontWeight: 600,
          fontFamily: 'var(--font-mono)',
        }}
      >
        {n}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--gray-50)' }}>
          {title}
        </div>
        {body}
        {hint && (
          <div className="faded" style={{ fontSize: 11, lineHeight: '17px' }}>
            {hint}
          </div>
        )}
      </div>
    </div>
  )
}

function CodeBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div
      style={{
        position: 'relative',
        padding: '10px 12px',
        paddingRight: 64,
        background: 'var(--bg-base)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--r-sm)',
        fontFamily: 'var(--font-mono)',
        fontSize: 12,
        color: 'var(--gray-100)',
        whiteSpace: 'pre',
        overflow: 'auto',
      }}
    >
      {text}
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => {
          navigator.clipboard.writeText(text)
          setCopied(true)
          setTimeout(() => setCopied(false), 1200)
        }}
        style={{
          position: 'absolute',
          top: 6,
          right: 6,
          padding: '2px 6px',
          fontSize: 11,
        }}
      >
        {copied ? 'Copied' : <><I.Copy size={11} /> Copy</>}
      </button>
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
