import { useState } from 'react'

import type { IntegrationConfig, IntegrationProvider } from '../../types/founder'

interface IntegrationCardProps {
  provider: IntegrationProvider
  label: string
  description: string
  accentClass: string
  config: IntegrationConfig | null
  onSave: (next: Partial<IntegrationConfig> & { apiKey?: string }) => Promise<void>
  onTest: () => Promise<{ ok: boolean; detail?: string }>
}

type TestStatus = 'idle' | 'testing' | 'ok' | 'failed'

export default function IntegrationCard({
  provider,
  label,
  description,
  accentClass,
  config,
  onSave,
  onTest,
}: IntegrationCardProps) {
  const [enabled, setEnabled] = useState(config?.enabled ?? false)
  const [baseUrl, setBaseUrl] = useState(config?.baseUrl ?? '')
  const [apiKey, setApiKey] = useState('')
  const [testStatus, setTestStatus] = useState<TestStatus>('idle')
  const [testDetail, setTestDetail] = useState<string | undefined>()
  const [saving, setSaving] = useState(false)

  const hasApiKey = config?.hasApiKey ?? false

  async function handleSave() {
    setSaving(true)
    try {
      await onSave({
        enabled,
        baseUrl: baseUrl || null,
        ...(apiKey ? { apiKey } : {}),
      })
      setApiKey('')
    } finally {
      setSaving(false)
    }
  }

  async function handleTest() {
    setTestStatus('testing')
    setTestDetail(undefined)
    const result = await onTest()
    setTestStatus(result.ok ? 'ok' : 'failed')
    setTestDetail(result.detail)
  }

  return (
    <section className={`rounded-lg border bg-bg-card p-4 ${accentClass}`}>
      <header className="mb-3 flex items-start justify-between">
        <div>
          <h3 className="text-base font-semibold text-text-primary">{label}</h3>
          <p className="text-xs text-text-tertiary">{description}</p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-4 w-4"
          />
          <span className="text-text-secondary">Enable</span>
        </label>
      </header>

      <div className="space-y-2">
        <label className="block">
          <span className="mb-1 block text-xs text-text-tertiary">Base URL</span>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={placeholderFor(provider)}
            className="w-full rounded border border-border bg-bg-input px-3 py-1.5 text-sm text-text-primary"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-xs text-text-tertiary">
            API key {hasApiKey && <span className="text-text-secondary">(stored)</span>}
          </span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={hasApiKey ? '••••••••' : 'paste to set'}
            className="w-full rounded border border-border bg-bg-input px-3 py-1.5 text-sm text-text-primary"
          />
        </label>

        {provider === 'bsupervisor' && (
          <BSupervisorExtras config={config} />
        )}
      </div>

      <div className="mt-3 flex items-center justify-between">
        <button
          type="button"
          onClick={handleTest}
          disabled={!enabled || !baseUrl}
          className="rounded border border-border px-3 py-1 text-xs text-text-secondary disabled:opacity-40"
        >
          {testStatus === 'testing' ? 'Testing…' : 'Test connection'}
        </button>

        <div className="flex items-center gap-2">
          <TestStatusBadge status={testStatus} detail={testDetail} />
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="rounded bg-accent px-3 py-1 text-xs font-semibold text-bg-primary disabled:opacity-40"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </section>
  )
}

function BSupervisorExtras({ config }: { config: IntegrationConfig | null }) {
  const timeoutMs = (config?.extraConfig?.timeout_ms as number | undefined) ?? 200
  const failMode = (config?.extraConfig?.fail_mode as string | undefined) ?? 'open'
  return (
    <div className="grid grid-cols-2 gap-2 pt-1 text-xs text-text-tertiary">
      <div>
        <span className="mb-1 block">Timeout</span>
        <span className="font-mono text-text-secondary">{timeoutMs} ms</span>
      </div>
      <div>
        <span className="mb-1 block">On timeout</span>
        <span className="font-mono text-text-secondary">fail-{failMode}</span>
      </div>
    </div>
  )
}

function TestStatusBadge({ status, detail }: { status: TestStatus; detail?: string }) {
  if (status === 'idle') return null
  const label =
    status === 'testing' ? 'Testing…' : status === 'ok' ? 'Reachable' : 'Unreachable'
  const color =
    status === 'ok'
      ? 'text-success'
      : status === 'failed'
      ? 'text-error'
      : 'text-text-tertiary'
  return (
    <span className={`text-xs ${color}`} title={detail}>
      {label}
    </span>
  )
}

function placeholderFor(provider: IntegrationProvider): string {
  switch (provider) {
    case 'bsage':
      return 'https://sage.bsvibe.dev'
    case 'bsgateway':
      return 'https://gateway.bsvibe.dev'
    case 'bsupervisor':
      return 'https://supervisor.bsvibe.dev'
  }
}
