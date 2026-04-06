import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Modal } from '../components/common'
import { executorConfigsApi } from '../api/executorConfigs'
import type { ExecutorConfig, ExecutorConfigCreate } from '../types/executor'
import Header from '../components/layout/Header'
// Worker guide uses the current origin — in production, frontend proxies /api to backend

const INPUT_CLASS =
  'w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary'

const EXECUTOR_TYPES = [
  { value: 'claude_api', label: 'LLM API', description: 'Any LLM via LiteLLM (Claude, GPT, Gemini, open-source). For both coding and non-coding tasks.' },
  { value: 'bsgateway', label: 'BSGateway', description: 'BSGateway proxy with automatic cost-optimized model routing' },
  { value: '_worker', label: 'Self-Hosted Worker', description: 'Run coding tasks on your machine via Claude Code, Codex, or OpenCode.' },
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
      <div className="p-8 max-w-3xl space-y-8">
        {/* Executor Configs */}
        <div>
          <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider mb-4">
            Registered Executors
          </h3>
          {isLoading ? (
            <p className="text-sm text-text-tertiary py-4">Loading...</p>
          ) : configs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-text-secondary bg-stitch-surface-low rounded-xl border border-stitch-outline-variant/10">
              <span className="material-symbols-outlined text-3xl mb-2 text-text-tertiary">settings_suggest</span>
              <p className="text-xs">No executors registered. Click "+ Register Executor" to add one.</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {configs.map((c) => (
                <ExecutorCard key={c.id} config={c} onEdit={openEdit} onDelete={handleDelete} />
              ))}
            </div>
          )}
        </div>

      </div>

      {/* Register / Edit Modal */}
      <Modal
        open={modalOpen}
        onClose={closeModal}
        title={editTarget ? `Edit — ${editTarget.name}` : 'Register Executor'}
        footer={
          formType === '_worker' && !editTarget ? (
            <Button variant="secondary" onClick={closeModal}>Close</Button>
          ) : (
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
          )
        }
      >
        {/* Executor type selector (create only) */}
        {!editTarget && (
          <div className="mb-4">
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

        {/* Worker guide (shown when _worker selected) */}
        {formType === '_worker' && !editTarget ? (
          <div className="space-y-4">
            <p className="text-sm text-text-secondary">
              Run coding tasks on your machine. Supports Claude Code {'>'} Codex {'>'} OpenCode (auto-detected).
            </p>
            <div className="bg-stitch-surface-lowest rounded-lg p-4 space-y-3">
              <div>
                <p className="text-[10px] uppercase tracking-widest text-text-tertiary font-bold mb-1">1. Install worker</p>
                <code className="block text-xs text-stitch-primary bg-stitch-surface rounded px-3 py-2 font-mono select-all">
                  curl -fsSL {window.location.origin}/worker/install.sh | bash
                </code>
              </div>
              <div>
                <p className="text-[10px] uppercase tracking-widest text-text-tertiary font-bold mb-1">2. Register & run</p>
                <code className="block text-xs text-stitch-primary bg-stitch-surface rounded px-3 py-2 font-mono select-all">
                  bsnexus-worker register{'\n'}cd my-project && bsnexus-worker run
                </code>
                <p className="text-[10px] text-text-tertiary mt-1.5">
                  To use a specific executor:
                </p>
                <div className="space-y-1 mt-1">
                  <code className="block text-xs text-text-secondary bg-stitch-surface rounded px-3 py-1.5 font-mono">
                    bsnexus-worker run --executor claude_code
                  </code>
                  <code className="block text-xs text-text-secondary bg-stitch-surface rounded px-3 py-1.5 font-mono">
                    bsnexus-worker run --executor codex
                  </code>
                  <code className="block text-xs text-text-secondary bg-stitch-surface rounded px-3 py-1.5 font-mono">
                    bsnexus-worker run --executor opencode
                  </code>
                </div>
              </div>
            </div>
            <p className="text-[10px] text-text-tertiary">
              Requires Python 3.11+ and a coding CLI (Claude Code, Codex, or OpenCode).
            </p>
          </div>
        ) : (
          /* Normal executor registration form */
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-text-secondary mb-1.5">Name</label>
              <input
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. GPT-4o Production"
                className={INPUT_CLASS}
              />
            </div>

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

            <div>
              <label className="block text-sm text-text-secondary mb-1.5">Description (optional)</label>
              <input
                value={formDescription}
                onChange={(e) => setFormDescription(e.target.value)}
                placeholder="Brief description"
                className={INPUT_CLASS}
              />
            </div>

            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <input
                type="checkbox"
                checked={formDefault}
                onChange={(e) => setFormDefault(e.target.checked)}
                className="rounded"
              />
              Set as default
            </label>
          </div>
        )}
      </Modal>
    </>
  )
}
