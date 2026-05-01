import { useState } from 'react'
import { useTranslations } from 'next-intl'

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

// Static (non-translatable) per-provider visual chrome. Translated
// label / blurb live on ``nexus.settings.integrations.*`` and are
// resolved inside the component via ``useTranslations``.
const VISUAL: Record<
  IntegrationProvider,
  {
    accent: string
    Icon: (p: { size?: number }) => React.ReactElement
  }
> = {
  bsage: {
    accent: accentHex.emerald,
    Icon: I.Brain,
  },
  bsupervisor: {
    accent: accentHex.rose,
    Icon: I.Shield,
  },
}

export default function IntegrationCard({
  provider,
  config,
  onSave,
  onTest,
}: IntegrationCardProps) {
  const t = useTranslations('nexus.settings.integrations')
  const tStatus = useTranslations('nexus.status')
  const tCommon = useTranslations('nexus.common')
  const visual = VISUAL[provider]
  const configKey = `${provider}:${config?.enabled ?? false}:${config?.base_url ?? ''}:${
    (config?.extra_config?.timeout_ms as number | undefined) ?? 200
  }:${(config?.extra_config?.fail_mode as string | undefined) ?? 'open'}`
  const [draft, setDraft] = useState({
    key: configKey,
    enabled: config?.enabled ?? false,
    baseUrl: config?.base_url ?? '',
    timeoutMs: (config?.extra_config?.timeout_ms as number | undefined) ?? 200,
    failMode:
      (config?.extra_config?.fail_mode as string | undefined) === 'closed'
        ? 'closed'
        : 'open',
  })
  if (draft.key !== configKey) {
    setDraft({
      key: configKey,
      enabled: config?.enabled ?? false,
      baseUrl: config?.base_url ?? '',
      timeoutMs: (config?.extra_config?.timeout_ms as number | undefined) ?? 200,
      failMode:
        (config?.extra_config?.fail_mode as string | undefined) === 'closed'
          ? 'closed'
          : 'open',
    })
  }
  const { enabled, baseUrl, timeoutMs, failMode } = draft
  const [apiKey, setApiKey] = useState('')
  const [testStatus, setTestStatus] = useState<TestStatus>('idle')
  const [testDetail, setTestDetail] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  async function handleToggle(next: boolean) {
    // Auto-save the toggle — flipping a switch should persist immediately.
    // URL / API key still require the Save button since they need input.
    setDraft((current) => ({ ...current, enabled: next }))
    setSaving(true)
    setSaved(false)
    try {
      await onSave({ enabled: next })
      setSaved(true)
      setTimeout(() => setSaved(false), 1800)
    } catch {
      setDraft((current) => ({ ...current, enabled: !next }))
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
    <div className="card" style={{ borderLeft: `3px solid ${visual.accent}` }}>
      <div className="card-hd">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 'var(--r-md)',
              background: `${visual.accent}20`,
              color: visual.accent,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <visual.Icon size={16} />
          </div>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--gray-50)' }}>
              {t(`${provider}.label`)}
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
                ? tStatus('healthy')
                : testStatus === 'unreachable'
                ? tStatus('unreachable')
                : testStatus === 'unauthorized'
                ? tStatus('unauthorized')
                : tStatus('enabled')
              : tStatus('disabled')}
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
          {t(`${provider}.blurb`)}
        </p>

        <Field label={t('baseUrlLabel')}>
          <input
            className="input"
            value={baseUrl}
            onChange={(e) =>
              setDraft((current) => ({ ...current, baseUrl: e.target.value }))
            }
            disabled={!enabled}
            placeholder={`https://${provider === 'bsage' ? 'sage' : 'supervisor'}.bsvibe.dev`}
          />
        </Field>

        <Field label={`${t('apiKeyLabel')} ${hasApiKey ? t('apiKeyStored') : ''}`}>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              className="input mono"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={!enabled}
              placeholder={hasApiKey ? t('apiKeyPlaceholderStored') : t('apiKeyPlaceholderEmpty')}
              type="password"
              style={{ fontSize: 12 }}
            />
            {hasApiKey && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={!enabled}
                onClick={() => setApiKey('')}
                title={t('rotateTitle')}
              >
                {tCommon('rotate')}
              </button>
            )}
          </div>
        </Field>

        {provider === 'bsupervisor' && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: 12,
            }}
          >
            <Field label={t('timeoutLabel')} hint={t('timeoutHint')}>
              <input
                className="input mono"
                type="number"
                value={timeoutMs}
                onChange={(e) =>
                  setDraft((current) => ({
                    ...current,
                    timeoutMs: Number(e.target.value),
                  }))
                }
                disabled={!enabled}
              />
            </Field>
            <Field label={t('failModeLabel')} hint={t('failModeHint')}>
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
                    onClick={() =>
                      setDraft((current) => ({ ...current, failMode: m }))
                    }
                    disabled={!enabled}
                  >
                    {m === 'open' ? t('failModeOpen') : t('failModeClosed')}
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
                {tCommon('testing')}
              </>
            ) : (
              <>
                <I.Zap size={12} /> {tCommon('test')}
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
              {tStatus(testStatus as never)}
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
              <I.Check size={12} /> {tCommon('saved')}
            </span>
          )}
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? tCommon('saving') : tCommon('save')}
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
          style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}
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
