import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Modal } from '../components/common'
import { executorConfigsApi } from '../api/executorConfigs'
import type { ExecutorConfig, ExecutorConfigCreate } from '../types/executor'
import Header from '../components/layout/Header'

const INPUT_CLASS =
  'w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary'

const EXECUTOR_TYPES = [
  { value: 'claude_api', label: 'LLM API', description: 'Any LLM via LiteLLM (Claude, GPT, Gemini, open-source). For both coding and non-coding tasks.' },
  { value: 'claude_code', label: 'Claude Code', description: 'Claude Code CLI — runs on local machine or self-hosted worker' },
  { value: 'bsgateway', label: 'BSGateway', description: 'BSGateway proxy with automatic cost-optimized model routing' },
] as const

interface ConfigField {
  key: string
  label: string
  type: 'text' | 'password' | 'select'
  placeholder?: string
  options?: { value: string; label: string }[]
}

const EXECUTOR_FIELDS: Record<string, ConfigField[]> = {
  claude_api: [
    { key: 'api_key', label: 'API Key', type: 'password', placeholder: 'sk-ant-..., sk-..., etc.' },
    { key: 'model', label: 'Model (LiteLLM format)', type: 'text', placeholder: 'anthropic/claude-sonnet-4-20250514' },
    { key: 'base_url', label: 'Base URL (optional)', type: 'text', placeholder: 'https://api.anthropic.com' },
    { key: 'temperature', label: 'Temperature', type: 'text', placeholder: '0.0 for coding, 0.7 for writing' },
    { key: 'max_tokens', label: 'Max Tokens', type: 'text', placeholder: '4096' },
  ],
  claude_code: [
    { key: 'execution_mode', label: 'Execution Mode', type: 'select', options: [{ value: 'self_hosted', label: 'Self-hosted (user machine)' }, { value: 'tenant', label: 'Tenant (server-side)' }] },
    { key: 'workspace_dir', label: 'Workspace Directory', type: 'text', placeholder: '/workspace' },
    { key: 'timeout_seconds', label: 'Timeout (seconds)', type: 'text', placeholder: '3600' },
    { key: 'skip_permissions', label: 'Skip Permissions', type: 'select', options: [{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }] },
  ],
  bsgateway: [
    { key: 'bsgateway_url', label: 'Gateway URL', type: 'text', placeholder: 'https://gateway.bsvibe.dev' },
    { key: 'bsgateway_api_key', label: 'API Key', type: 'password', placeholder: 'bsg-...' },
    { key: 'routing_hint', label: 'Routing Strategy', type: 'select', options: [{ value: 'auto', label: 'Auto (cost-optimized)' }, { value: 'performance', label: 'Performance' }, { value: 'economy', label: 'Economy' }] },
  ],
}

function ExecutorCard({
  config,
  onEdit,
  onDelete,
}: {
  config: ExecutorConfig
  onEdit: (c: ExecutorConfig) => void
  onDelete: (id: string) => void
}) {
  const typeLabel = EXECUTOR_TYPES.find((t) => t.value === config.executor_type)?.label ?? config.executor_type
  return (
    <div className="bg-stitch-surface-low rounded-xl p-5 border border-stitch-outline-variant/10">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-bold text-text-primary">{config.name}</h4>
          {config.is_default && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-stitch-primary/20 text-stitch-primary font-bold">DEFAULT</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => onEdit(config)} className="p-1 text-text-tertiary hover:text-stitch-primary transition-colors" title="Edit">
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>edit</span>
          </button>
          <button onClick={() => onDelete(config.id)} className="p-1 text-text-tertiary hover:text-stitch-error transition-colors" title="Delete">
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>delete</span>
          </button>
        </div>
      </div>
      <span className="text-xs px-2 py-0.5 rounded-full bg-stitch-secondary-container text-stitch-on-secondary-container">
        {typeLabel}
      </span>
      {config.description && (
        <p className="text-xs text-text-tertiary mt-2">{config.description}</p>
      )}
    </div>
  )
}

export default function SettingsPage() {
  const queryClient = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<ExecutorConfig | null>(null)

  // Form state
  const [formName, setFormName] = useState('')
  const [formType, setFormType] = useState('claude_api')
  const [formConfig, setFormConfig] = useState<Record<string, string>>({})
  const [formDescription, setFormDescription] = useState('')
  const [formDefault, setFormDefault] = useState(false)

  const { data: configs = [], isLoading } = useQuery({
    queryKey: ['executor-configs'],
    queryFn: executorConfigsApi.list,
  })

  const createMutation = useMutation({
    mutationFn: (data: ExecutorConfigCreate) => executorConfigsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executor-configs'] })
      closeModal()
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Record<string, unknown> }) =>
      executorConfigsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executor-configs'] })
      closeModal()
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => executorConfigsApi.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['executor-configs'] }),
  })

  const openCreate = () => {
    setEditTarget(null)
    setFormName('')
    setFormType('claude_api')
    setFormConfig({})
    setFormDescription('')
    setFormDefault(false)
    setModalOpen(true)
  }

  const openEdit = (config: ExecutorConfig) => {
    setEditTarget(config)
    setFormName(config.name)
    setFormType(config.executor_type)
    setFormConfig(Object.fromEntries(Object.entries(config.config).map(([k, v]) => [k, String(v)])))
    setFormDescription(config.description || '')
    setFormDefault(config.is_default)
    setModalOpen(true)
  }

  const closeModal = () => {
    setModalOpen(false)
    setEditTarget(null)
  }

  const handleSave = () => {
    const cleanConfig = Object.fromEntries(
      Object.entries(formConfig).filter(([, v]) => v !== '')
    )
    if (editTarget) {
      updateMutation.mutate({
        id: editTarget.id,
        data: { name: formName, config: cleanConfig, description: formDescription || undefined, is_default: formDefault },
      })
    } else {
      createMutation.mutate({
        name: formName,
        executor_type: formType,
        config: cleanConfig,
        description: formDescription || undefined,
        is_default: formDefault,
      })
    }
  }

  const handleDelete = (id: string) => {
    if (confirm('Delete this executor configuration?')) {
      deleteMutation.mutate(id)
    }
  }

  const fields = EXECUTOR_FIELDS[formType] || []

  return (
    <>
      <Header
        title="Settings"
        action={
          <button
            onClick={openCreate}
            className="bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container px-4 py-1.5 rounded-md text-sm font-bold shadow-lg shadow-stitch-primary/20 hover:opacity-90 transition-opacity"
          >
            + Register Executor
          </button>
        }
      />
      <div className="p-8 max-w-3xl">
        {isLoading ? (
          <p className="text-sm text-text-tertiary py-4">Loading executor configurations...</p>
        ) : configs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-text-secondary">
            <span className="material-symbols-outlined text-4xl mb-2 text-text-tertiary">settings_suggest</span>
            <p className="text-sm mb-1">No executors registered yet</p>
            <p className="text-xs text-text-tertiary">Register an executor to start assigning agents</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {configs.map((c) => (
              <ExecutorCard key={c.id} config={c} onEdit={openEdit} onDelete={handleDelete} />
            ))}
          </div>
        )}
      </div>

      {/* Register / Edit Modal */}
      <Modal
        open={modalOpen}
        onClose={closeModal}
        title={editTarget ? `Edit — ${editTarget.name}` : 'Register Executor'}
        footer={
          <>
            <Button variant="secondary" onClick={closeModal}>Cancel</Button>
            <Button
              variant="primary"
              onClick={handleSave}
              loading={createMutation.isPending || updateMutation.isPending}
            >
              {editTarget ? 'Save' : 'Register'}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {/* Name */}
          <div>
            <label className="block text-sm text-text-secondary mb-1.5">Name</label>
            <input
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder="e.g. Claude Code (local)"
              className={INPUT_CLASS}
            />
          </div>

          {/* Executor Type (only on create) */}
          {!editTarget && (
            <div>
              <label className="block text-sm text-text-secondary mb-1.5">Executor Type</label>
              <select
                value={formType}
                onChange={(e) => { setFormType(e.target.value); setFormConfig({}) }}
                className={INPUT_CLASS}
              >
                {EXECUTOR_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
              <p className="mt-1 text-xs text-text-tertiary">
                {EXECUTOR_TYPES.find((t) => t.value === formType)?.description}
              </p>
            </div>
          )}

          {/* Type-specific config fields */}
          {fields.length > 0 && (
            <div className="border-t border-stitch-outline-variant/20 pt-4 space-y-3">
              <p className="text-[10px] uppercase tracking-widest text-text-secondary font-bold">Configuration</p>
              {fields.map((field) => (
                <div key={field.key}>
                  <label className="block text-xs text-text-tertiary mb-1">{field.label}</label>
                  {field.type === 'select' && field.options ? (
                    <select
                      value={formConfig[field.key] || field.options[0]?.value || ''}
                      onChange={(e) => setFormConfig((c) => ({ ...c, [field.key]: e.target.value }))}
                      className={INPUT_CLASS}
                    >
                      {field.options.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type={field.type}
                      value={formConfig[field.key] || ''}
                      onChange={(e) => setFormConfig((c) => ({ ...c, [field.key]: e.target.value }))}
                      placeholder={field.placeholder}
                      className={INPUT_CLASS}
                    />
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Description */}
          <div>
            <label className="block text-sm text-text-secondary mb-1.5">Description (optional)</label>
            <input
              value={formDescription}
              onChange={(e) => setFormDescription(e.target.value)}
              placeholder="Brief description of this executor setup"
              className={INPUT_CLASS}
            />
          </div>

          {/* Default toggle */}
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={formDefault}
              onChange={(e) => setFormDefault(e.target.checked)}
              className="rounded"
            />
            Set as default for this executor type
          </label>
        </div>
      </Modal>
    </>
  )
}
