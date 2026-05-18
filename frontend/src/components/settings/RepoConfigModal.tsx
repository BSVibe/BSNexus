'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Modal } from '../common/Modal'
import {
  repoConfigApi,
  type RepoConfigResponse,
} from '../../api/repoConfig'

/**
 * RepoConfigModal — G8.0.1 per-project repo binding admin.
 *
 * Binding a repo connects the project to it: the backend flips
 * ``workspace_type`` to ``github_connected`` so the orchestrator
 * clones the repo into the workspace, and future branches/commits/PRs
 * land on it. Clearing the binding reverts to a managed workspace.
 *
 * Token is tri-state on save:
 *   - blank input + no stored token → never sent (preserve = none)
 *   - blank input + stored token + "clear" toggle → sent as ``null`` (clear)
 *   - blank input + stored token + no clear → field omitted (preserve)
 *   - non-empty input → sent as the new value
 *
 * Mirrors the executor-config UX so the founder learns one shape.
 */
export default function RepoConfigModal({
  open,
  onClose,
  projectId,
}: {
  open: boolean
  onClose: () => void
  projectId: string
}) {
  const t = useTranslations('nexus.project.repoConfig')
  const tCommon = useTranslations('nexus.common')
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useQuery<RepoConfigResponse | null>({
    queryKey: ['repo-config', projectId],
    queryFn: () => repoConfigApi.get(projectId),
    enabled: open,
  })

  const [repoUrl, setRepoUrl] = useState('')
  const [branch, setBranch] = useState('main')
  const [tokenInput, setTokenInput] = useState('')
  const [clearToken, setClearToken] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // React 19 compiler-friendly resync: reset the draft when the modal
  // opens or when the server-side binding changes. We track a tiny
  // fingerprint instead of using ``useEffect`` because setting state
  // synchronously in ``useEffect`` trips ``react-hooks/set-state-in-effect``.
  const fingerprint = open
    ? `${data?.repo_url ?? ''}|${data?.branch ?? ''}|${data?.has_token ? '1' : '0'}`
    : 'closed'
  const [seenFingerprint, setSeenFingerprint] = useState(fingerprint)
  if (fingerprint !== seenFingerprint) {
    setSeenFingerprint(fingerprint)
    if (open) {
      setRepoUrl(data?.repo_url ?? '')
      setBranch(data?.branch ?? 'main')
      setTokenInput('')
      setClearToken(false)
      setSaveError(null)
    }
  }

  const upsert = useMutation({
    mutationFn: async () => {
      const body: {
        repo_url: string
        branch: string
        token?: string | null
      } = { repo_url: repoUrl.trim(), branch: branch.trim() }
      if (tokenInput) body.token = tokenInput
      else if (clearToken && data?.has_token) body.token = null
      return repoConfigApi.upsert(projectId, body)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repo-config', projectId] })
      onClose()
    },
    onError: (err: unknown) => {
      const msg =
        err instanceof Error ? err.message : (t('saveFailed') as string)
      setSaveError(msg)
    },
  })

  const clear = useMutation({
    mutationFn: () => repoConfigApi.clear(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repo-config', projectId] })
      onClose()
    },
    onError: (err: unknown) => {
      const msg =
        err instanceof Error ? err.message : (t('clearFailed') as string)
      setSaveError(msg)
    },
  })

  const disabled = upsert.isPending || clear.isPending
  const canSave = repoUrl.trim().length > 0 && branch.trim().length > 0

  return (
    <Modal
      open={open}
      onClose={disabled ? () => {} : onClose}
      title={t('title')}
      footer={
        <>
          {data && (
            <button
              type="button"
              className="btn btn-ghost"
              style={{ color: 'var(--rose-500)' }}
              disabled={disabled}
              onClick={() => clear.mutate()}
            >
              {t('clearBinding')}
            </button>
          )}
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onClose}
            disabled={disabled}
          >
            {tCommon('cancel')}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={disabled || !canSave}
            onClick={() => upsert.mutate()}
          >
            {upsert.isPending ? tCommon('saving') : tCommon('save')}
          </button>
        </>
      }
    >
      {isLoading ? (
        <p className="faded" style={{ fontSize: 13 }}>
          {tCommon('loading')}
        </p>
      ) : error ? (
        <p style={{ fontSize: 13, color: 'var(--rose-500)' }}>
          {t('loadFailed')}
        </p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <p className="faded" style={{ fontSize: 13, margin: 0 }}>
            {t('subtitle')}
          </p>

          <Field
            label={t('field.repoUrl')}
            hint={t('field.repoUrlHint')}
            value={repoUrl}
            onChange={setRepoUrl}
            placeholder="https://github.com/owner/repo"
            disabled={disabled}
          />

          <Field
            label={t('field.branch')}
            hint={t('field.branchHint')}
            value={branch}
            onChange={setBranch}
            placeholder="main"
            disabled={disabled}
          />

          <div>
            <label
              style={{ display: 'block', fontSize: 12, marginBottom: 4 }}
            >
              {t('field.token')}
              {data?.has_token && (
                <span
                  className="faded"
                  style={{ marginLeft: 6, fontSize: 11 }}
                >
                  {t('tokenStored')}
                </span>
              )}
            </label>
            <input
              type="password"
              autoComplete="off"
              className="input"
              value={tokenInput}
              onChange={(e) => {
                setTokenInput(e.target.value)
                if (e.target.value) setClearToken(false)
              }}
              placeholder={
                data?.has_token
                  ? t('tokenPlaceholderStored')
                  : t('tokenPlaceholderEmpty')
              }
              disabled={disabled}
            />
            {data?.has_token && !tokenInput && (
              <label
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  marginTop: 6,
                  fontSize: 12,
                }}
              >
                <input
                  type="checkbox"
                  checked={clearToken}
                  onChange={(e) => setClearToken(e.target.checked)}
                  disabled={disabled}
                />
                {t('clearStoredToken')}
              </label>
            )}
          </div>

          {saveError && (
            <p style={{ fontSize: 12, color: 'var(--rose-500)', margin: 0 }}>
              {saveError}
            </p>
          )}
        </div>
      )}
    </Modal>
  )
}

function Field({
  label,
  hint,
  value,
  onChange,
  placeholder,
  disabled,
}: {
  label: string
  hint: string
  value: string
  onChange: (next: string) => void
  placeholder: string
  disabled: boolean
}) {
  return (
    <div>
      <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>
        {label}
      </label>
      <input
        type="text"
        className="input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
      <p
        className="faded"
        style={{ fontSize: 11, marginTop: 4, marginBottom: 0 }}
      >
        {hint}
      </p>
    </div>
  )
}
