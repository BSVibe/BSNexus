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

const BSGATEWAY_DEFAULT_URL = 'https://gateway.bsvibe.dev'

/**
 * LLMDispatchSection — single per-tenant executor config card.
 *
 * Two-path LLM dispatch (CLAUDE.md MUST rule): the founder picks
 * between BSGateway (SaaS — SSO covers auth, token optional) and
 * litellm direct (provider URL + model + key). Switching paths is a
 * kind change on the same row, not a new row — exactly one config
 * per tenant.
 *
 * G7.5e:
 *   - The "사용 중" toggle is gone. Existence of the row IS the
 *     activation signal — there's only one config, so a toggle is
 *     redundant.
 *   - BSGateway defaults to the SaaS endpoint; the registration token
 *     field is optional (SSO carries auth on the SaaS path).
 */
export default function LLMDispatchSection() {
  const t = useTranslations('nexus.settings.llmDispatch')
  const tCommon = useTranslations('nexus.common')
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
    />
  )
}

interface DispatchCardProps {
  config: ExecutorConfigResponse | null
  onSaved: () => void
  t: (k: string) => string
  tCommon: (k: string) => string
}

function DispatchCard({ config, onSaved, t, tCommon }: DispatchCardProps) {
  const upstream = useUpstreamFingerprint(config)
  const [draft, setDraft] = useState(() => buildDraft(config))
  const [seenUpstream, setSeenUpstream] = useState(upstream)
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [clearKeyOnSave, setClearKeyOnSave] = useState(false)

  // Resync only when the upstream config row changes — not on every
  // local edit.
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

  function setKind(kind: ExecutorKind) {
    setDraft((d) => ({
      ...d,
      kind,
      // BSGateway defaults to the SaaS endpoint when there's nothing
      // entered yet. Don't clobber a URL the founder already typed.
      baseUrl:
        kind === 'bsgateway' && !d.baseUrl ? BSGATEWAY_DEFAULT_URL : d.baseUrl,
    }))
  }

  async function handleSave() {
    setSaving(true)
    setSaved(false)
    try {
      const body: ExecutorConfigUpdate = {
        kind: draft.kind,
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

  const accent = accentHex.amber
  const hasApiKey = !!config?.has_api_key && !clearKeyOnSave
  const apiKeyLabel =
    draft.kind === 'bsgateway' ? t('field.registrationToken') : t('field.providerKey')
  const apiKeyOptional = draft.kind === 'bsgateway'

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
      </div>

      <div
        className="card-bd"
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
        }}
      >
        <KindPicker
          value={draft.kind}
          onChange={setKind}
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
            placeholder={
              draft.kind === 'bsgateway'
                ? BSGATEWAY_DEFAULT_URL
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
              placeholder="ollama_chat/qwen3-coder:30b"
            />
          </Field>
        )}

        <Field
          label={`${apiKeyLabel} ${
            hasApiKey
              ? t('apiKeyStored')
              : apiKeyOptional
              ? t('apiKeyOptional')
              : ''
          }`}
          hint={apiKeyOptional && !hasApiKey ? t('field.bsgatewaySsoHint') : undefined}
        >
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              className="input mono"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                hasApiKey ? t('apiKeyPlaceholderStored') : t('apiKeyPlaceholderEmpty')
              }
              style={{ fontSize: 12 }}
            />
            {hasApiKey && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
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
  baseUrl: string
  model: string
}

function buildDraft(config: ExecutorConfigResponse | null): Draft {
  return {
    kind: config?.kind ?? 'bsgateway',
    baseUrl:
      config?.base_url ?? (config ? '' : BSGATEWAY_DEFAULT_URL),
    model: config?.model ?? '',
  }
}

function useUpstreamFingerprint(config: ExecutorConfigResponse | null): string {
  if (!config) return 'null'
  return `${config.kind}|${config.base_url ?? ''}|${
    config.model ?? ''
  }|${config.has_api_key}`
}

function KindPicker({
  value,
  onChange,
  t,
}: {
  value: ExecutorKind
  onChange: (k: ExecutorKind) => void
  t: (k: string) => string
}) {
  const KINDS: ExecutorKind[] = ['bsgateway', 'llm_api']
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
      <div className="kind-picker">
        {KINDS.map((k) => {
          const active = value === k
          return (
            <button
              key={k}
              type="button"
              className="btn btn-sm kind-picker__option"
              onClick={() => onChange(k)}
              style={{
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
              <span
                className="faded"
                style={{
                  fontSize: 11,
                  whiteSpace: 'normal',
                  textAlign: 'left',
                  lineHeight: 1.35,
                }}
              >
                {t(`kind.${k}.blurb`)}
              </span>
            </button>
          )
        })}
      </div>
    </div>
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
          flexWrap: 'wrap',
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
