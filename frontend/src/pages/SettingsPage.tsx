import { useState, useEffect } from 'react'
import { Button } from '../components/common'
import { settingsApi } from '../api/settings'
import Header from '../components/layout/Header'

interface LLMSettings {
  api_key: string
  model: string
  base_url: string
  default_executor_type: string
}

const INPUT_CLASS =
  'w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary'

const EXECUTOR_OPTIONS = [
  { value: 'claude_api', label: 'Claude API', description: 'Direct Claude API calls via LiteLLM' },
  { value: 'claude_code', label: 'Claude Code', description: 'Claude Code CLI for code generation tasks' },
  { value: 'bsgateway', label: 'BSGateway', description: 'BSGateway proxy with cost tracking' },
  { value: 'codex', label: 'Codex', description: 'OpenAI Codex for code completion' },
  { value: 'generic_llm', label: 'Generic LLM', description: 'Any LiteLLM-compatible model' },
] as const

interface ExecutorConfigFields {
  label: string
  fields: { key: string; label: string; type: 'text' | 'password' | 'select'; placeholder?: string; options?: { value: string; label: string }[] }[]
}

const EXECUTOR_CONFIG_FIELDS: Record<string, ExecutorConfigFields> = {
  claude_api: {
    label: 'Claude API',
    fields: [
      { key: 'model', label: 'Model', type: 'text', placeholder: 'anthropic/claude-sonnet-4-20250514' },
      { key: 'max_tokens', label: 'Max Tokens', type: 'text', placeholder: '4096' },
    ],
  },
  claude_code: {
    label: 'Claude Code',
    fields: [
      { key: 'execution_mode', label: 'Execution Mode', type: 'select', options: [{ value: 'tenant', label: 'Tenant (cloud)' }, { value: 'self_hosted', label: 'Self-hosted (local)' }] },
      { key: 'workspace_dir', label: 'Workspace Directory', type: 'text', placeholder: '/workspace' },
      { key: 'timeout_seconds', label: 'Timeout (seconds)', type: 'text', placeholder: '3600' },
      { key: 'skip_permissions', label: 'Skip Permissions', type: 'select', options: [{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }] },
    ],
  },
  bsgateway: {
    label: 'BSGateway',
    fields: [
      { key: 'bsgateway_url', label: 'Gateway URL', type: 'text', placeholder: 'https://gateway.bsvibe.dev' },
      { key: 'bsgateway_api_key', label: 'API Key', type: 'password', placeholder: 'bsg-...' },
      { key: 'routing_hint', label: 'Routing', type: 'select', options: [{ value: 'auto', label: 'Auto (cost-optimized)' }, { value: 'performance', label: 'Performance (best model)' }, { value: 'economy', label: 'Economy (cheapest)' }] },
    ],
  },
  codex: {
    label: 'Codex',
    fields: [
      { key: 'execution_mode', label: 'Execution Mode', type: 'select', options: [{ value: 'tenant', label: 'Tenant (cloud)' }, { value: 'self_hosted', label: 'Self-hosted' }] },
      { key: 'model', label: 'Model', type: 'text', placeholder: 'openai/codex-mini' },
      { key: 'max_tokens', label: 'Max Tokens', type: 'text', placeholder: '4096' },
    ],
  },
  generic_llm: {
    label: 'Generic LLM',
    fields: [
      { key: 'model', label: 'Model', type: 'text', placeholder: 'openai/gpt-4o' },
      { key: 'temperature', label: 'Temperature', type: 'text', placeholder: '0.7' },
      { key: 'max_tokens', label: 'Max Tokens', type: 'text', placeholder: '4096' },
    ],
  },
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<LLMSettings>({
    api_key: '',
    model: '',
    base_url: '',
    default_executor_type: 'claude_api',
  })
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [apiKeyTouched, setApiKeyTouched] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(null)
    settingsApi
      .get()
      .then((data) => {
        setSettings({
          api_key: data.llm_api_key || '',
          model: data.llm_model || '',
          base_url: data.llm_base_url || '',
          default_executor_type: data.default_executor_type || 'claude_api',
        })
      })
      .catch(() => setError('Failed to load settings'))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      const update: Record<string, string> = {}
      if (apiKeyTouched && settings.api_key) {
        update.llm_api_key = settings.api_key
      }
      if (settings.model) update.llm_model = settings.model
      if (settings.base_url) update.llm_base_url = settings.base_url
      update.default_executor_type = settings.default_executor_type
      await settingsApi.update(update)
      setSuccess(true)
      setApiKeyTouched(false)
      setTimeout(() => setSuccess(false), 3000)
    } catch {
      setError('Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  const selectedExecutor = EXECUTOR_CONFIG_FIELDS[settings.default_executor_type]

  return (
    <>
      <Header
        title="Settings"
        action={
          <Button variant="primary" onClick={handleSave} loading={saving}>
            Save Settings
          </Button>
        }
      />
      <div className="p-8 max-w-2xl space-y-6">
        {error && <p className="text-sm text-stitch-error mb-4">{error}</p>}
        {success && <p className="text-sm text-green-400 mb-4">Settings saved successfully.</p>}

        {loading ? (
          <p className="text-sm text-text-tertiary py-4">Loading settings...</p>
        ) : (
          <>
            {/* LLM Configuration */}
            <div className="bg-stitch-surface-container rounded-xl p-6 space-y-6">
              <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider">
                LLM Configuration
              </h3>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm text-text-secondary mb-1.5">API Key</label>
                  <input
                    type="password"
                    value={settings.api_key}
                    onChange={(e) => {
                      setApiKeyTouched(true)
                      setSettings((s) => ({ ...s, api_key: e.target.value }))
                    }}
                    placeholder="sk-..."
                    className={INPUT_CLASS}
                  />
                  {settings.api_key && !apiKeyTouched && (
                    <p className="mt-1 text-xs text-text-tertiary">
                      Saved (masked). Enter a new key to change it.
                    </p>
                  )}
                </div>

                <div>
                  <label className="block text-sm text-text-secondary mb-1.5">Model</label>
                  <input
                    type="text"
                    value={settings.model}
                    onChange={(e) => setSettings((s) => ({ ...s, model: e.target.value }))}
                    placeholder="anthropic/claude-sonnet-4-20250514"
                    className={INPUT_CLASS}
                  />
                </div>

                <div>
                  <label className="block text-sm text-text-secondary mb-1.5">Base URL (Optional)</label>
                  <input
                    type="text"
                    value={settings.base_url}
                    onChange={(e) => setSettings((s) => ({ ...s, base_url: e.target.value }))}
                    placeholder="https://your-api.example.com"
                    className={INPUT_CLASS}
                  />
                </div>

                <div>
                  <label className="block text-sm text-text-secondary mb-1.5">Default Executor</label>
                  <select
                    value={settings.default_executor_type}
                    onChange={(e) =>
                      setSettings((s) => ({ ...s, default_executor_type: e.target.value }))
                    }
                    className={INPUT_CLASS}
                  >
                    {EXECUTOR_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-text-tertiary">
                    {EXECUTOR_OPTIONS.find((o) => o.value === settings.default_executor_type)
                      ?.description ?? ''}
                  </p>
                </div>
              </div>
            </div>

            {/* Executor-specific Configuration */}
            <div className="bg-stitch-surface-container rounded-xl p-6 space-y-6">
              <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider">
                Executor Configuration — {selectedExecutor?.label}
              </h3>
              <p className="text-xs text-text-tertiary -mt-4">
                These settings apply to all agents using {selectedExecutor?.label}. Agents can override individual values.
              </p>

              {selectedExecutor && (
                <div className="space-y-4">
                  {selectedExecutor.fields.map((field) => (
                    <div key={field.key}>
                      <label className="block text-sm text-text-secondary mb-1.5">{field.label}</label>
                      {field.type === 'select' && field.options ? (
                        <select className={INPUT_CLASS} defaultValue={field.options[0]?.value}>
                          {field.options.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type={field.type}
                          placeholder={field.placeholder}
                          className={INPUT_CLASS}
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </>
  )
}
