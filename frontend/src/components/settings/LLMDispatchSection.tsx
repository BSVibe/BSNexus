'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { I } from '../../lib/icons'
import { accentHex } from '../../lib/tone'
import {
  executorConfigApi,
  type ExecutorConfigResponse,
  type ExecutorConfigUpdate,
  type ExecutorKind,
} from '../../api/executors'

/**
 * LLMDispatchSection — single per-tenant executor config card.
 *
 * Two-path LLM dispatch (CLAUDE.md MUST rule): the founder picks
 * between routing through BSGateway's worker pool or calling litellm
 * directly. Switching paths is a kind change on the same row, not a
 * new row — exactly one config per tenant.
 *
 * Without this, no LLM can run; quality engineering is blocked.
 */
export default function LLMDispatchSection() {
  const t = useTranslations('nexus.settings.llmDispatch')
  const tCommon = useTranslations('nexus.common')
  const tStatus = useTranslations('nexus.status')
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useQuery<ExecutorConfigResponse | null>({
    queryKey: ['executor-config'],
    queryFn: executorConfigApi.get,
  })

  if (isLoading) {
    return (
      <div style={{ padding: 16 }}>
        <p className="faded" style={{ fontSize: 13 }}>
          {tCommon('loading')}
        </p>
      </div>
    )
  }
  if (error) {
    return (
      <div style={{ padding: 16 }}>
        <div
          className="card"
          style={{
            padding: 16,
            fontSize: 13,
            color: 'var(--text-tertiary)',
            borderColor: 'rgba(244,63,94,0.3)',
          }}
        >
          {t('loadFailed')}
        </div>
      </div>
    )
  }

  return (
    <DispatchCard
      config={data ?? null}
      onSaved={() =>
        queryClient.invalidateQueries({ queryKey: ['executor-config'] })
      }
      t={t}
      tCommon={tCommon}
      tStatus={tStatus}
    />
  )
}

interface DispatchCardProps {
  config: ExecutorConfigResponse | null
  onSaved: () => void
  t: (k: string) => string
  tCommon: (k: string) => string
  tStatus: (k: string) => string
}

function DispatchCard({ config, onSaved, t, tCommon, tStatus }: DispatchCardProps) {
  const upstream = useUpstreamFingerprint(config)
  const [draft, setDraft] = useState(() => buildDraft(config))
  const [seenUpstream, setSeenUpstream] = useState(upstream)
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [clearKeyOnSave, setClearKeyOnSave] = useState(false)

  // Resync only when the upstream config row changes — not on every
  // local edit. Conflating the two reverts user input mid-edit.
  if (upstream !== seenUpstream) {
    setSeenUpstream(upstream)
    setDraft(buildDraft(config))
    setApiKey('')
    setClearKeyOnSave(false)
  }

  const mutation = useMutation({
    mutationFn: (body: ExecutorConfigUpdate) => executorConfigApi.upsert(body),
    onSuccess: onSaved,
  })

  async function handleSave() {
    setSaving(true)
    setSaved(false)
    try {
      const body: ExecutorConfigUpdate = {
        kind: draft.kind,
        enabled: draft.enabled,
        base_url: draft.baseUrl || null,
        model: draft.kind === 'llm_api' ? draft.model || null : null,
      }
      if (apiKey) {
        body.api_key = apiKey
      } else if (clearKeyOnSave) {
        body.api_key = null
      }
      await mutation.mutateAsync(body)
      setApiKey('')
      setClearKeyOnSave(false)
      setSaved(true)
      setTimeout(() => setSaved(false), 1800)
    } finally {
      setSaving(false)
    }
  }

  async function handleToggleEnabled(next: boolean) {
    setSaving(true)
    setDraft((d) => ({ ...d, enabled: next }))
    try {
      await mutation.mutateAsync({ kind: draft.kind, enabled: next })
      setSaved(true)
      setTimeout(() => setSaved(false), 1800)
    } catch {
      setDraft((d) => ({ ...d, enabled: !next }))
    } finally {
      setSaving(false)
    }
  }

  const accent = accentHex.amber
  const hasApiKey = !!config?.has_api_key && !clearKeyOnSave
  const enabled = draft.enabled

  return (
    <div className="card" style={{ borderLeft: `3px solid ${accent}` }}>
      <div className="card-hd">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 'var(--r-md)',
              background: `${accent}20`,
              color: accent,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <I.Zap size={16} />
          </div>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--gray-50)' }}>
              {t('title')}
            </div>
            <div className="faded" style={{ fontSize: 11 }}>
              {t('subtitle')}
            </div>
          </div>
        </div>
        <Toggle
          checked={enabled}
          onChange={handleToggleEnabled}
          disabled={saving}
          label={enabled ? tStatus('enabled') : tStatus('disabled')}
        />
      </div>

      <div
        className="card-bd"
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          opacity: enabled ? 1 : 0.6,
        }}
      >
        <KindPicker
          value={draft.kind}
          onChange={(kind) => setDraft((d) => ({ ...d, kind }))}
          disabled={!enabled}
          t={t}
        />

        <Field
          label={
            draft.kind === 'bsgateway' ? t('field.gatewayUrl') : t('field.providerUrl')
          }
        >
          <input
            className="input"
            value={draft.baseUrl}
            onChange={(e) => setDraft((d) => ({ ...d, baseUrl: e.target.value }))}
            disabled={!enabled}
            placeholder={
              draft.kind === 'bsgateway'
                ? 'https://gateway.bsvibe.dev'
                : 'http://host.docker.internal:11434'
            }
          />
        </Field>

        {draft.kind === 'llm_api' && (
          <Field label={t('field.model')} hint={t('field.modelHint')}>
            <input
              className="input mono"
              value={draft.model}
              onChange={(e) => setDraft((d) => ({ ...d, model: e.target.value }))}
              disabled={!enabled}
              placeholder="ollama_chat/qwen3-coder:30b"
            />
          </Field>
        )}

        <Field
          label={
            draft.kind === 'bsgateway'
              ? `${t('field.registrationToken')} ${
                  hasApiKey ? t('apiKeyStored') : ''
                }`
              : `${t('field.providerKey')} ${hasApiKey ? t('apiKeyStored') : ''}`
          }
        >
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              className="input mono"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={!enabled}
              placeholder={
                hasApiKey ? t('apiKeyPlaceholderStored') : t('apiKeyPlaceholderEmpty')
              }
              style={{ fontSize: 12 }}
            />
            {hasApiKey && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={!enabled}
                onClick={() => setClearKeyOnSave(true)}
                title={t('clearKeyTitle')}
              >
                {tCommon('rotate')}
              </button>
            )}
          </div>
        </Field>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            paddingTop: 8,
            borderTop: '1px solid var(--border-subtle)',
          }}
        >
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

interface Draft {
  kind: ExecutorKind
  enabled: boolean
  baseUrl: string
  model: string
}

function buildDraft(config: ExecutorConfigResponse | null): Draft {
  return {
    kind: config?.kind ?? 'llm_api',
    enabled: config?.enabled ?? false,
    baseUrl: config?.base_url ?? '',
    model: config?.model ?? '',
  }
}

function useUpstreamFingerprint(config: ExecutorConfigResponse | null): string {
  if (!config) return 'null'
  return `${config.kind}|${config.enabled}|${config.base_url ?? ''}|${
    config.model ?? ''
  }|${config.has_api_key}`
}

function KindPicker({
  value,
  onChange,
  disabled,
  t,
}: {
  value: ExecutorKind
  onChange: (k: ExecutorKind) => void
  disabled?: boolean
  t: (k: string) => string
}) {
  const KINDS: ExecutorKind[] = ['llm_api', 'bsgateway']
  return (
    <div>
      <div
        style={{
          fontSize: 12,
          color: 'var(--text-secondary)',
          marginBottom: 6,
        }}
      >
        {t('kindLabel')}
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 8,
        }}
      >
        {KINDS.map((k) => {
          const active = value === k
          return (
            <button
              key={k}
              type="button"
              className="btn btn-sm"
              disabled={disabled}
              onClick={() => onChange(k)}
              style={{
                flexDirection: 'column',
                alignItems: 'flex-start',
                padding: '10px 12px',
                gap: 4,
                minHeight: 56,
                background: active ? 'var(--bg-hover)' : 'var(--bg-elevated)',
                border: `1px solid ${
                  active ? accentHex.amber : 'var(--border-default)'
                }`,
                color: active ? 'var(--gray-50)' : 'var(--gray-300)',
              }}
            >
              <span style={{ fontWeight: 600, fontSize: 13 }}>
                {t(`kind.${k}.label`)}
              </span>
              <span className="faded" style={{ fontSize: 11 }}>
                {t(`kind.${k}.blurb`)}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function Toggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  label: string
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      aria-pressed={checked}
      style={{
        minWidth: 44,
        minHeight: 44,
        padding: '0 10px',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        background: 'transparent',
        border: 'none',
        color: 'var(--text-secondary)',
        fontSize: 12,
        cursor: disabled ? 'wait' : 'pointer',
        opacity: disabled ? 0.7 : 1,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 32,
          height: 18,
          borderRadius: 99,
          background: checked ? 'var(--blue-500)' : 'var(--gray-700)',
          position: 'relative',
          display: 'inline-block',
          transition: 'background var(--t-fast) var(--ease)',
        }}
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
      </span>
      {label}
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
