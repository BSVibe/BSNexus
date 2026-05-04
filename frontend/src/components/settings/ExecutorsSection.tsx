import { useState } from 'react'
import { useTranslations } from 'next-intl'
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
import RemoteWorkersSection from './RemoteWorkersSection'

// Static field metadata only — labels/descriptions/placeholders come
// from the i18n bundle (``nexus.settings.executors.field.*`` and
// ``nexus.settings.executors.type.*``).
type ExecFieldKey =
  | 'model'
  | 'api_key'
  | 'base_url'
  | 'gateway_url'
  | 'gateway_api_key'

type ExecField = {
  key: string
  fieldKey: ExecFieldKey
  secret?: boolean
}

type ExecTypeMeta = {
  value: ExecutorType
  fields: ExecField[]
}

// Two top-level kinds — taxonomy collapse, 2026-05-04. Distinguished
// by *infra dependency*, not capability:
//   bsgateway = BSVibe infra path (BSGateway worker pool routes the
//               underlying CLI agent / model)
//   llm_api   = BSVibe-optional path (direct litellm + MCP tool loop)
// Both honor full MCP / Decisions / artifact UX.
const EXEC_TYPES: ExecTypeMeta[] = [
  {
    value: 'bsgateway',
    fields: [
      { key: 'bsgateway_url', fieldKey: 'gateway_url' },
      { key: 'bsgateway_api_key', fieldKey: 'gateway_api_key', secret: true },
      { key: 'model', fieldKey: 'model' },
    ],
  },
  {
    value: 'llm_api',
    fields: [
      { key: 'model', fieldKey: 'model' },
      { key: 'api_key', fieldKey: 'api_key', secret: true },
      { key: 'base_url', fieldKey: 'base_url' },
    ],
  },
]

const EXEC_FIELD_LABEL: Record<ExecFieldKey, string> = {
  model: 'model',
  api_key: 'apiKey',
  base_url: 'baseUrl',
  gateway_url: 'gatewayUrl',
  gateway_api_key: 'gatewayApiKey',
}

const EXEC_FIELD_PLACEHOLDER: Record<ExecFieldKey, string> = {
  model: 'modelPlaceholder',
  api_key: 'apiKeyPlaceholder',
  base_url: 'baseUrlPlaceholder',
  gateway_url: 'gatewayUrlPlaceholder',
  gateway_api_key: 'gatewayApiKeyPlaceholder',
}

export default function ExecutorsSection() {
  const t = useTranslations('nexus.settings.executors')
  const tCommon = useTranslations('nexus.common')
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

  const selectMutation = useMutation({
    mutationFn: (id: string) =>
      executorConfigsApi.update(id, { is_selected: true }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['executor-configs'] }),
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="faded" style={{ fontSize: 12 }}>
          {t('registeredCount', { count: configs.length })}
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => setCreateOpen(true)}
        >
          <I.Plus size={12} /> {t('registerButton')}
        </button>
      </div>

      {isLoading && (
        <p className="faded" style={{ fontSize: 13 }}>
          {tCommon('loading')}
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
          {t('emptyHint')}
        </div>
      )}

      {configs.map((cfg) => (
        <ExecutorCard
          key={cfg.id}
          config={cfg}
          onDelete={() => deleteMutation.mutate(cfg.id)}
          onSelect={() => selectMutation.mutate(cfg.id)}
          onEdit={() => setEditing(cfg)}
        />
      ))}

      <div
        style={{
          marginTop: 16,
          paddingTop: 20,
          borderTop: '1px solid var(--border-subtle)',
        }}
      >
        <RemoteWorkersSection />
      </div>

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
  onSelect,
  onEdit,
}: {
  config: ExecutorConfig
  onDelete: () => void
  onSelect: () => void
  onEdit: () => void
}) {
  const t = useTranslations('nexus.settings.executors')
  const tStatus = useTranslations('nexus.status')
  const tCommon = useTranslations('nexus.common')
  return (
    <div
      className="card"
      style={{
        padding: 14,
        display: 'grid',
        gridTemplateColumns: '20px 1fr',
        gap: 12,
        alignItems: 'start',
        borderColor: config.is_selected
          ? 'var(--blue-500)'
          : 'var(--border-subtle)',
      }}
    >
      <SelectDot
        selected={config.is_selected}
        onSelect={onSelect}
        label={config.name}
      />
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <I.Brain size={14} />
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--gray-50)' }}>
            {config.name}
          </span>
          {config.is_selected && (
            <Badge tone="blue" dot>
              {tStatus('inUse')}
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
          <button type="button" className="btn btn-ghost btn-sm" onClick={onEdit}>
            <I.Settings size={12} /> {tCommon('edit')}
          </button>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              if (confirm(t('deleteConfirm', { name: config.name }))) onDelete()
            }}
            style={{ color: 'var(--brand-rose, #f43f5e)' }}
          >
            <I.X size={12} /> {tCommon('delete')}
          </button>
        </div>
      </div>
    </div>
  )
}

function SelectDot({
  selected,
  onSelect,
  label,
}: {
  selected: boolean
  onSelect: () => void
  label: string
}) {
  const t = useTranslations('nexus.settings.executors.card')
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={selected ? t('selectedAria', { name: label }) : t('selectAria', { name: label })}
      title={selected ? t('selectedTitle') : t('selectableTitle')}
      onClick={() => {
        if (!selected) onSelect()
      }}
      style={{
        width: 16,
        height: 16,
        marginTop: 2,
        padding: 0,
        borderRadius: '50%',
        border: `2px solid ${
          selected ? 'var(--blue-500)' : 'var(--border-strong, #3f3f46)'
        }`,
        background: 'transparent',
        cursor: selected ? 'default' : 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {selected && (
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: 'var(--blue-500)',
          }}
        />
      )}
    </button>
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
  const tModal = useTranslations('nexus.settings.executors.modal')
  const tType = useTranslations('nexus.settings.executors.type')
  const tField = useTranslations('nexus.settings.executors.field')
  const tCommon = useTranslations('nexus.common')
  const [type, setType] = useState<ExecutorType>(
    // Default to ``bsgateway`` for new configs — the canonical path
    // when BSVibe infra is available. ``generic_llm`` is the explicit
    // opt-out for self-hosted-without-BSVibe deployments.
    existing?.executor_type ?? 'bsgateway',
  )
  const [name, setName] = useState(existing?.name ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [useThis, setUseThis] = useState(existing?.is_selected ?? false)
  const [cfg, setCfg] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {}
    if (existing) {
      Object.entries(existing.config).forEach(([k, v]) => {
        initial[k] = String(v ?? '')
      })
    }
    return initial
  })

  const meta = EXEC_TYPES.find((tt) => tt.value === type)

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
      is_selected: useThis,
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
            {existing ? tModal('editTitle', { name: existing.name }) : tModal('registerTitle')}
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
                {tModal('typeLabel')}
              </span>
              <select
                className="input"
                value={type}
                onChange={(e) => {
                  setType(e.target.value as ExecutorType)
                  setCfg({})
                }}
              >
                {EXEC_TYPES.map((tt) => (
                  <option key={tt.value} value={tt.value}>
                    {tType(`${tt.value}.label`)}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{tModal('nameLabel')}</span>
            <input
              className="input"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={tModal('namePlaceholder')}
            />
          </label>

          {meta?.fields.map((f) => (
            <label
              key={f.key}
              style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
            >
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {tField(EXEC_FIELD_LABEL[f.fieldKey])}
              </span>
              <input
                className="input mono"
                type={f.secret ? 'password' : 'text'}
                value={cfg[f.key] ?? ''}
                onChange={(e) =>
                  setCfg((prev) => ({ ...prev, [f.key]: e.target.value }))
                }
                placeholder={tField(EXEC_FIELD_PLACEHOLDER[f.fieldKey])}
                style={{ fontSize: 12 }}
              />
            </label>
          ))}

          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {tModal('descriptionLabel')}
            </span>
            <input
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={tModal('descriptionPlaceholder')}
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
              checked={useThis}
              onChange={(e) => setUseThis(e.target.checked)}
            />
            {tModal('useThis')}
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
              {tCommon('cancel')}
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!name.trim() || pending}
            >
              {pending ? tCommon('saving') : existing ? tCommon('save') : tCommon('register')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
