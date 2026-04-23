import { useEffect, useState } from 'react'

import { Badge } from '../common/Badge'
import { I } from '../../lib/icons'
import { accentHex, type Tone } from '../../lib/tone'
import type {
  IntegrationConfigResponse,
  IntegrationConfigUpdate,
  IntegrationTestResult,
} from '../../api/integrations'
import type { IntegrationProvider } from '../../types/founder'

type TestStatus = 'idle' | 'testing' | IntegrationTestResult['status']

interface IntegrationCardProps {
  provider: IntegrationProvider
  config: IntegrationConfigResponse | null
  onSave: (next: IntegrationConfigUpdate) => Promise<void>
  onTest: () => Promise<IntegrationTestResult>
}

const META: Record<
  IntegrationProvider,
  {
    label: string
    accent: string
    Icon: (p: { size?: number }) => React.ReactElement
    blurb: string
  }
> = {
  bsage: {
    label: 'BSage',
    accent: accentHex.emerald,
    Icon: I.Brain,
    blurb: 'Graph-backed project memory. When enabled, runs pull relevant notes into their composition.',
  },
  bsupervisor: {
    label: 'BSupervisor',
    accent: accentHex.rose,
    Icon: I.Shield,
    blurb: 'Sync pre-run rule evaluation (<50ms target). Fail-open by default; configurable.',
  },
}

export default function IntegrationCard({
  provider,
  config,
  onSave,
  onTest,
}: IntegrationCardProps) {
  const meta = META[provider]
  const [enabled, setEnabled] = useState(config?.enabled ?? false)
  const [baseUrl, setBaseUrl] = useState(config?.base_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [timeoutMs, setTimeoutMs] = useState<number>(
    (config?.extra_config?.timeout_ms as number | undefined) ?? 200,
  )
  const [failMode, setFailMode] = useState<'open' | 'closed'>(
    ((config?.extra_config?.fail_mode as string | undefined) === 'closed'
      ? 'closed'
      : 'open') as 'open' | 'closed',
  )
  const [testStatus, setTestStatus] = useState<TestStatus>('idle')
  const [testDetail, setTestDetail] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setEnabled(config?.enabled ?? false)
    setBaseUrl(config?.base_url ?? '')
    setTimeoutMs(((config?.extra_config?.timeout_ms as number | undefined) ?? 200))
    setFailMode(
      ((config?.extra_config?.fail_mode as string | undefined) === 'closed'
        ? 'closed'
        : 'open') as 'open' | 'closed',
    )
  }, [config])

  async function handleToggle(next: boolean) {
    // Auto-save the toggle — flipping a switch should persist immediately.
    // URL / API key still require the Save button since they need input.
    setEnabled(next)
    setSaving(true)
    setSaved(false)
    try {
      await onSave({ enabled: next })
      setSaved(true)
      setTimeout(() => setSaved(false), 1800)
    } catch {
      setEnabled(!next) // revert optimistic UI
    } finally {
      setSaving(false)
    }
  }

  const hasApiKey = config?.has_api_key ?? false
  const tone: Tone = !enabled
    ? 'gray'
    : testStatus === 'healthy'
    ? 'emerald'
    : testStatus === 'unreachable' || testStatus === 'unauthorized'
    ? 'rose'
    : 'emerald'

  async function handleSave() {
    setSaving(true)
    setSaved(false)
    try {
      const body: IntegrationConfigUpdate = {
        enabled,
        base_url: baseUrl || null,
      }
      if (apiKey) body.api_key = apiKey
      if (provider === 'bsupervisor') {
        body.extra_config = { timeout_ms: timeoutMs, fail_mode: failMode }
      }
      await onSave(body)
      setSaved(true)
      setApiKey('')
      setTimeout(() => setSaved(false), 1800)
    } finally {
      setSaving(false)
    }
  }

  async function handleTest() {
    setTestStatus('testing')
    setTestDetail(null)
    const result = await onTest()
    setTestStatus(result.status)
    setTestDetail(result.detail)
  }

  return (
    <div className="card" style={{ borderLeft: `3px solid ${meta.accent}` }}>
      <div className="card-hd">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 'var(--r-md)',
              background: `${meta.accent}20`,
              color: meta.accent,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <meta.Icon size={16} />
          </div>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--gray-50)' }}>
              {meta.label}
            </div>
            <div className="faded mono" style={{ fontSize: 11 }}>
              {provider}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Badge tone={tone} dot>
            {enabled
              ? testStatus === 'healthy'
                ? 'healthy'
                : testStatus === 'unreachable'
                ? 'unreachable'
                : testStatus === 'unauthorized'
                ? 'unauthorized'
                : 'enabled'
              : 'disabled'}
          </Badge>
          <Toggle checked={enabled} onChange={handleToggle} disabled={saving} />
        </div>
      </div>
      <div
        className="card-bd"
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
          opacity: enabled ? 1 : 0.5,
        }}
      >
        <p className="faded" style={{ fontSize: 12, margin: 0 }}>
          {meta.blurb}
        </p>

        <Field label="Base URL">
          <input
            className="input"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            disabled={!enabled}
            placeholder={`https://${provider === 'bsage' ? 'sage' : 'supervisor'}.bsvibe.dev`}
          />
        </Field>

        <Field label={`API key ${hasApiKey ? '(stored)' : ''}`}>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              className="input mono"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={!enabled}
              placeholder={hasApiKey ? '••••••••' : 'bsk_...'}
              type="password"
              style={{ fontSize: 12 }}
            />
            {hasApiKey && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={!enabled}
                onClick={() => setApiKey('')}
                title="Clear stored key"
              >
                Rotate
              </button>
            )}
          </div>
        </Field>

        {provider === 'bsupervisor' && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 12,
            }}
          >
            <Field label="Timeout (ms)" hint="Default 200ms">
              <input
                className="input mono"
                type="number"
                value={timeoutMs}
                onChange={(e) => setTimeoutMs(Number(e.target.value))}
                disabled={!enabled}
              />
            </Field>
            <Field label="Fail mode" hint="open = allow on error · closed = deny">
              <div
                style={{
                  display: 'flex',
                  gap: 4,
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--border-default)',
                  borderRadius: 'var(--r-md)',
                  padding: 2,
                }}
              >
                {(['open', 'closed'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    className="btn btn-sm"
                    style={{
                      flex: 1,
                      justifyContent: 'center',
                      background:
                        failMode === m ? 'var(--bg-hover)' : 'transparent',
                      color:
                        failMode === m ? 'var(--gray-50)' : 'var(--gray-400)',
                    }}
                    onClick={() => setFailMode(m)}
                    disabled={!enabled}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </Field>
          </div>
        )}

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            paddingTop: 8,
            borderTop: '1px solid var(--border-subtle)',
          }}
        >
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleTest}
            disabled={!enabled || !baseUrl || testStatus === 'testing'}
          >
            {testStatus === 'testing' ? (
              <>
                <span
                  style={{
                    width: 6,
                    height: 6,
                    background: 'var(--gray-400)',
                    borderRadius: 99,
                    animation: 'pulse 1s infinite',
                  }}
                />
                Testing…
              </>
            ) : (
              <>
                <I.Zap size={12} /> Test connection
              </>
            )}
          </button>
          {testStatus !== 'idle' && testStatus !== 'testing' && (
            <span
              className="faded"
              style={{
                fontSize: 11,
                color:
                  testStatus === 'healthy'
                    ? '#6ee7b7'
                    : testStatus === 'unauthorized' || testStatus === 'unreachable'
                    ? '#fda4af'
                    : undefined,
              }}
              title={testDetail ?? undefined}
            >
              {testStatus}
            </span>
          )}
          <span style={{ flex: 1 }} />
          {saved && (
            <span
              style={{
                fontSize: 12,
                color: '#6ee7b7',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <I.Check size={12} /> Saved
            </span>
          )}
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      style={{
        width: 32,
        height: 18,
        borderRadius: 99,
        background: checked ? 'var(--blue-500)' : 'var(--gray-700)',
        position: 'relative',
        border: 'none',
        cursor: disabled ? 'wait' : 'pointer',
        transition: 'background var(--t-fast) var(--ease)',
        opacity: disabled ? 0.7 : 1,
      }}
      aria-pressed={checked}
    >
      <span
        style={{
          position: 'absolute',
          top: 2,
          left: checked ? 16 : 2,
          width: 14,
          height: 14,
          borderRadius: 99,
          background: '#fff',
          transition: 'left var(--t-fast) var(--ease)',
        }}
      />
    </button>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 4,
        }}
      >
        <label
          style={{ fontSize: 12, color: 'var(--text-secondary)' }}
        >
          {label}
        </label>
        {hint && (
          <span className="faded" style={{ fontSize: 11 }}>
            {hint}
          </span>
        )}
      </div>
      {children}
    </div>
  )
}
