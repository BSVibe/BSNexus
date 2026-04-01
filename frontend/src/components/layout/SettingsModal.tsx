import { useState, useEffect } from 'react'
import { Modal, Button } from '../common'
import { settingsApi } from '../../api/settings'

interface SettingsModalProps {
  open: boolean
  onClose: () => void
}

interface LLMSettings {
  api_key: string
  model: string
  base_url: string
}

export function SettingsModal({ open, onClose }: SettingsModalProps) {
  const [settings, setSettings] = useState<LLMSettings>({
    api_key: '',
    model: '',
    base_url: '',
  })
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Track whether the API key field was touched by the user
  const [apiKeyTouched, setApiKeyTouched] = useState(false)

  useEffect(() => {
    if (open) {
      setLoading(true)
      setError(null)
      setApiKeyTouched(false)
      settingsApi.get()
        .then((data) => {
          setSettings({
            api_key: data.llm_api_key || '',
            model: data.llm_model || '',
            base_url: data.llm_base_url || '',
          })
        })
        .catch(() => {
          setError('Failed to load settings')
        })
        .finally(() => setLoading(false))
    }
  }, [open])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const update: Record<string, string> = {}
      // Only send API key if user actually typed a new value
      if (apiKeyTouched && settings.api_key) {
        update.llm_api_key = settings.api_key
      }
      if (settings.model) {
        update.llm_model = settings.model
      }
      if (settings.base_url) {
        update.llm_base_url = settings.base_url
      }
      await settingsApi.update(update)
      onClose()
    } catch {
      setError('Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Settings"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={handleSave} loading={saving}>Save Settings</Button>
        </>
      }
    >
      <div className="space-y-6">
        {error && (
          <p className="text-sm text-stitch-error">{error}</p>
        )}
        {loading ? (
          <p className="text-sm text-text-tertiary py-4 text-center">Loading settings...</p>
        ) : (
          <div>
            <h3 className="text-sm font-medium text-text-primary mb-4">LLM Configuration</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm text-text-secondary mb-1.5">API Key</label>
                <input
                  type="password"
                  value={settings.api_key}
                  onChange={e => {
                    setApiKeyTouched(true)
                    setSettings(s => ({ ...s, api_key: e.target.value }))
                  }}
                  placeholder="sk-..."
                  className="w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary"
                />
                {settings.api_key && !apiKeyTouched && (
                  <p className="mt-1 text-xs text-text-tertiary">Saved (masked). Enter a new key to change it.</p>
                )}
              </div>
              <div>
                <label className="block text-sm text-text-secondary mb-1.5">Model</label>
                <input
                  type="text"
                  value={settings.model}
                  onChange={e => setSettings(s => ({ ...s, model: e.target.value }))}
                  placeholder="anthropic/claude-sonnet-4-20250514"
                  className="w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary"
                />
              </div>
              <div>
                <label className="block text-sm text-text-secondary mb-1.5">Base URL (Optional)</label>
                <input
                  type="text"
                  value={settings.base_url}
                  onChange={e => setSettings(s => ({ ...s, base_url: e.target.value }))}
                  placeholder="https://your-litellm-proxy.com"
                  className="w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary"
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}
