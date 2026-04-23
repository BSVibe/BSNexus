import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge } from '../common/Badge'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import {
  executorConfigsApi,
  type ExecutorConfig,
  type ExecutorConfigCreate,
  type ExecutorType,
} from '../../api/executorConfigs'

type ExecTypeMeta = {
  value: ExecutorType
  label: string
  description: string
  fields: Array<{ key: string; label: string; placeholder?: string; secret?: boolean }>
}

const EXEC_TYPES: ExecTypeMeta[] = [
  {
    value: 'generic_llm',
    label: 'LLM (LiteLLM direct)',
    description: 'Any LiteLLM provider. Used for coding and non-coding runs alike.',
    fields: [
      { key: 'model', label: 'Model', placeholder: 'openai/gpt-4o, anthropic/claude-sonnet-4-6, …' },
      { key: 'api_key', label: 'API key', placeholder: 'sk-…', secret: true },
      { key: 'base_url', label: 'Base URL (optional)', placeholder: 'https://api.anthropic.com' },
    ],
  },
  {
    value: 'bsgateway',
    label: 'BSGateway (cost-aware routing)',
    description: 'Proxy all LLM calls through BSGateway for cost-optimized routing.',
    fields: [
      { key: 'bsgateway_url', label: 'Gateway URL', placeholder: 'https://gateway.bsvibe.dev' },
      { key: 'bsgateway_api_key', label: 'API key', placeholder: 'bsg-…', secret: true },
    ],
  },
  {
    value: 'claude_code',
    label: 'Claude Code (local)',
    description: 'Run coding tasks through the claude-code CLI on a self-hosted worker.',
    fields: [],
  },
  {
    value: 'codex',
    label: 'Codex CLI',
    description: 'Run coding tasks through a codex worker (future).',
    fields: [],
  },
]

export default function ExecutorsSection() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<ExecutorConfig | null>(null)

  const { data: configs = [], isLoading } = useQuery<ExecutorConfig[]>({
    queryKey: ['executor-configs'],
    queryFn: executorConfigsApi.list,
  })

  const createMutation = useMutation({
    mutationFn: (body: ExecutorConfigCreate) => executorConfigsApi.create(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executor-configs'] })
      setCreateOpen(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => executorConfigsApi.delete(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['executor-configs'] }),
  })

  const setDefaultMutation = useMutation({
    mutationFn: (id: string) =>
      executorConfigsApi.update(id, { is_default: true }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['executor-configs'] }),
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="faded" style={{ fontSize: 12 }}>
          {configs.length} registered · one can be set as default
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => setCreateOpen(true)}
        >
          <I.Plus size={12} /> Register executor
        </button>
      </div>

      {isLoading && (
        <p className="faded" style={{ fontSize: 13 }}>
          Loading…
        </p>
      )}

      {!isLoading && configs.length === 0 && (
        <div
          className="card"
          style={{
            padding: 32,
            textAlign: 'center',
            fontSize: 13,
            color: 'var(--text-tertiary)',
          }}
        >
          No executors registered yet. Every LLM run falls back to the hard-coded
          default until you add one here.
        </div>
      )}

      {configs.map((cfg) => (
        <ExecutorCard
          key={cfg.id}
          config={cfg}
          onDelete={() => deleteMutation.mutate(cfg.id)}
          onSetDefault={() => setDefaultMutation.mutate(cfg.id)}
          onEdit={() => setEditing(cfg)}
        />
      ))}

      {createOpen && (
        <ExecutorModal
          existing={null}
          onClose={() => setCreateOpen(false)}
          onSubmit={(body) => createMutation.mutate(body)}
          pending={createMutation.isPending}
          error={createMutation.isError ? (createMutation.error as Error).message : null}
        />
      )}
      {editing && (
        <ExecutorModal
          existing={editing}
          onClose={() => setEditing(null)}
          onSubmit={async (body) => {
            await executorConfigsApi.update(editing.id, {
              name: body.name,
              config: body.config,
              description: body.description,
            })
            queryClient.invalidateQueries({ queryKey: ['executor-configs'] })
            setEditing(null)
          }}
          pending={false}
          error={null}
        />
      )}
    </div>
  )
}

function ExecutorCard({
  config,
  onDelete,
  onSetDefault,
  onEdit,
}: {
  config: ExecutorConfig
  onDelete: () => void
  onSetDefault: () => void
  onEdit: () => void
}) {
  const meta = EXEC_TYPES.find((t) => t.value === config.executor_type)
  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <I.Brain size={14} />
        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--gray-50)' }}>
          {config.name}
        </span>
        {config.is_default && (
          <Badge tone="blue" dot>
            default
          </Badge>
        )}
        <span className="mono faded" style={{ fontSize: 11 }}>
          {truncId(config.id)}
        </span>
        <span style={{ flex: 1 }} />
        <span className="faded" style={{ fontSize: 11 }}>
          {relTime(config.updated_at)}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Badge tone="gray" square>
          {config.executor_type}
        </Badge>
        {meta && (
          <span className="faded" style={{ fontSize: 12 }}>
            {meta.description}
          </span>
        )}
      </div>
      {config.description && (
        <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
          {config.description}
        </p>
      )}
      <div
        style={{
          marginTop: 10,
          paddingTop: 10,
          borderTop: '1px solid var(--border-subtle)',
          display: 'flex',
          gap: 6,
        }}
      >
        {!config.is_default && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onSetDefault}
          >
            Make default
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-sm" onClick={onEdit}>
          <I.Settings size={12} /> Edit
        </button>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => {
            if (confirm(`Delete executor "${config.name}"?`)) onDelete()
          }}
          style={{ color: 'var(--brand-rose, #f43f5e)' }}
        >
          <I.X size={12} /> Delete
        </button>
      </div>
    </div>
  )
}

function ExecutorModal({
  existing,
  onClose,
  onSubmit,
  pending,
  error,
}: {
  existing: ExecutorConfig | null
  onClose: () => void
  onSubmit: (body: ExecutorConfigCreate) => void | Promise<void>
  pending: boolean
  error: string | null
}) {
  const [type, setType] = useState<ExecutorType>(
    existing?.executor_type ?? 'generic_llm',
  )
  const [name, setName] = useState(existing?.name ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [isDefault, setIsDefault] = useState(existing?.is_default ?? false)
  const [cfg, setCfg] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {}
    if (existing) {
      Object.entries(existing.config).forEach(([k, v]) => {
        initial[k] = String(v ?? '')
      })
    }
    return initial
  })

  const meta = EXEC_TYPES.find((t) => t.value === type)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const cleanConfig: Record<string, unknown> = {}
    Object.entries(cfg).forEach(([k, v]) => {
      if (v.trim()) cleanConfig[k] = v.trim()
    })
    onSubmit({
      name: name.trim(),
      executor_type: type,
      config: cleanConfig,
      description: description.trim() || null,
      is_default: isDefault,
    })
  }

  return (
    <div className="cmd-mask" onClick={onClose}>
      <div className="cmd" style={{ width: 560 }} onClick={(e) => e.stopPropagation()}>
        <div
          style={{
            padding: '14px 16px',
            borderBottom: '1px solid var(--border-subtle)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            {existing ? `Edit — ${existing.name}` : 'Register executor'}
          </div>
          <button type="button" className="btn btn-icon" onClick={onClose}>
            <I.X size={14} />
          </button>
        </div>
        <form
          onSubmit={handleSubmit}
          style={{
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          {!existing && (
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Type
              </span>
              <select
                className="input"
                value={type}
                onChange={(e) => {
                  setType(e.target.value as ExecutorType)
                  setCfg({})
                }}
              >
                {EXEC_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
              {meta && (
                <span className="faded" style={{ fontSize: 11 }}>
                  {meta.description}
                </span>
              )}
            </label>
          )}

          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Name</span>
            <input
              className="input"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. GPT-4o production"
            />
          </label>

          {meta?.fields.map((f) => (
            <label
              key={f.key}
              style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
            >
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {f.label}
              </span>
              <input
                className="input mono"
                type={f.secret ? 'password' : 'text'}
                value={cfg[f.key] ?? ''}
                onChange={(e) =>
                  setCfg((prev) => ({ ...prev, [f.key]: e.target.value }))
                }
                placeholder={f.placeholder}
                style={{ fontSize: 12 }}
              />
            </label>
          ))}

          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Description (optional)
            </span>
            <input
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Brief note"
            />
          </label>

          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 13,
              color: 'var(--text-secondary)',
            }}
          >
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            Set as default executor
          </label>

          {error && (
            <div
              style={{
                fontSize: 12,
                color: '#fda4af',
                padding: '6px 8px',
                background: 'rgba(244,63,94,0.08)',
                border: '1px solid rgba(244,63,94,0.3)',
                borderRadius: 'var(--r-sm)',
              }}
            >
              {error}
            </div>
          )}

          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 8,
              paddingTop: 8,
              borderTop: '1px solid var(--border-subtle)',
            }}
          >
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!name.trim() || pending}
            >
              {pending ? 'Saving…' : existing ? 'Save' : 'Register'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
