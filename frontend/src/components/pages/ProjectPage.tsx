'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { I } from '../../lib/icons'
import { Modal } from '../common/Modal'
import FilesView from '../files/FilesView'
import ProgressView from '../progress/ProgressView'
import DecisionsView from '../decisions/DecisionsView'
import Inspector from '../inside/Inspector'
import { projectsApi, type Project } from '../../api/projects'
import { workspaceFilesApi } from '../../api/workspaceFiles'
import { decisionsApi, deliverablesApi } from '../../api/founder'

type TabId = 'progress' | 'files' | 'decisions' | 'inside'

function parseTab(raw: string | null): TabId {
  if (raw === 'files' || raw === 'decisions' || raw === 'inside') return raw
  return 'progress'
}

export default function ProjectPage() {
  const t = useTranslations('nexus.project')
  const tCommon = useTranslations('nexus.common')
  const params = useParams<{ projectId?: string | string[] }>()
  // App Router catch-all yields an array; the dynamic segment yields a
  // string. Normalise to string | undefined.
  const rawProjectId = params?.projectId
  const projectId = Array.isArray(rawProjectId) ? rawProjectId[0] : rawProjectId
  const search = useSearchParams()
  const tab = parseTab(search.get('tab'))
  const focusRequestId = search.get('focusRequest')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const router = useRouter()
  const queryClient = useQueryClient()

  // Build a /projects/:id?tab=…&focusRequest=… URL given a partial
  // override. Centralised so the inline next/navigation calls below
  // don't duplicate the assembly.
  const buildProjectUrl = useCallback((next: URLSearchParams): string => {
    const qs = next.toString()
    return qs ? `/projects/${projectId}?${qs}` : `/projects/${projectId}`
  }, [projectId])

  const { data: project } = useQuery<Project>({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId!),
    enabled: Boolean(projectId),
  })

  const deleteMutation = useMutation({
    mutationFn: () => projectsApi.delete(projectId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setConfirmDelete(false)
      router.push('/')
    },
  })

  const { data: deliverables = [] } = useQuery({
    queryKey: ['deliverables', projectId],
    queryFn: () => deliverablesApi.listForProject(projectId!),
    enabled: Boolean(projectId),
    // Live updates land via the SSE stream wired up in Layout; no
    // polling needed. The query is invalidated on relevant events.
  })

  const { data: decisions = [] } = useQuery({
    queryKey: ['decisions', projectId],
    queryFn: () => decisionsApi.listForProject(projectId!),
    enabled: Boolean(projectId),
    // Live updates land via the SSE stream wired up in Layout; no
    // polling needed. The query is invalidated on relevant events.
  })

  const { data: files = [] } = useQuery({
    queryKey: ['workspace-files', projectId],
    queryFn: () => workspaceFilesApi.list(projectId!),
    enabled: Boolean(projectId),
    // Live updates land via the SSE stream wired up in Layout; no
    // polling needed. The query is invalidated on relevant events.
  })

  const openDecisions = useMemo(
    () => decisions.filter((d) => !d.resolved_at).length,
    [decisions],
  )

  function setTab(id: TabId) {
    const next = new URLSearchParams(search.toString())
    if (id === 'progress') next.delete('tab')
    else next.set('tab', id)
    if (id !== 'inside') next.delete('focusRequest')
    router.replace(buildProjectUrl(next))
  }

  useEffect(() => {
    function onOpenInside(e: Event) {
      const ce = e as CustomEvent<{ requestId?: string }>
      const rid = ce.detail?.requestId
      const next = new URLSearchParams(search.toString())
      next.set('tab', 'inside')
      if (rid) next.set('focusRequest', rid)
      else next.delete('focusRequest')
      router.replace(buildProjectUrl(next))
    }
    document.addEventListener('bsn:open-inside', onOpenInside as EventListener)
    return () =>
      document.removeEventListener(
        'bsn:open-inside',
        onOpenInside as EventListener,
      )
  }, [search, router, buildProjectUrl])

  if (!projectId) {
    return (
      <div
        style={{
          padding: 48,
          textAlign: 'center',
          color: 'var(--text-tertiary)',
        }}
      >
        {t('noProjectSelected')}
      </div>
    )
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
      }}
    >
      <div className="tabs" style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
        <TabButton
          label={t('tab.progress')}
          icon={<I.Timeline size={14} />}
          active={tab === 'progress'}
          onClick={() => setTab('progress')}
          count={deliverables.length || null}
        />
        <TabButton
          label={t('tab.files')}
          icon={<I.Doc size={14} />}
          active={tab === 'files'}
          onClick={() => setTab('files')}
          count={files.length || null}
        />
        <TabButton
          label={t('tab.decisions')}
          icon={<I.Inbox size={14} />}
          active={tab === 'decisions'}
          onClick={() => setTab('decisions')}
          count={openDecisions || null}
          toneRose={openDecisions > 0}
        />
        <TabButton
          label={t('tab.inside')}
          icon={<I.Eye size={14} />}
          active={tab === 'inside'}
          onClick={() => setTab('inside')}
        />
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="btn btn-icon"
          title={tCommon('delete')}
          style={{ color: 'var(--color-rose)', marginRight: 8 }}
          onClick={() => setConfirmDelete(true)}
        >
          <I.Trash size={14} />
        </button>
      </div>

      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title={t('deleteModal.title')}
        footer={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setConfirmDelete(false)}
              disabled={deleteMutation.isPending}
            >
              {tCommon('cancel')}
            </button>
            <button
              type="button"
              className="btn btn-primary"
              style={{ background: 'var(--color-rose)', borderColor: 'var(--color-rose)' }}
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? tCommon('deleting') : t('deleteModal.submit')}
            </button>
          </>
        }
      >
        <p style={{ color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          {t('deleteModal.body', {
            name: project?.name ?? t('deleteModal.fallbackName'),
          })}
        </p>
        {deleteMutation.isError && (
          <p style={{ color: 'var(--color-rose)', marginTop: 12, fontSize: 13 }}>
            {t('deleteModal.errorPrefix')} {(deleteMutation.error as Error)?.message}
          </p>
        )}
      </Modal>

      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {tab === 'progress' && <ProgressView projectId={projectId} />}
        {tab === 'files' && (
          <FilesView projectId={projectId} projectName={project?.name ?? t('fallbackTitle')} />
        )}
        {tab === 'decisions' && <DecisionsView projectId={projectId} />}
        {tab === 'inside' && (
          <Inspector projectId={projectId} focusRequestId={focusRequestId} />
        )}
      </div>
    </div>
  )
}

function TabButton({
  label,
  icon,
  active,
  onClick,
  count,
  toneRose,
}: {
  label: string
  icon: React.ReactNode
  active: boolean
  onClick: () => void
  count?: number | null
  toneRose?: boolean
}) {
  return (
    <button
      type="button"
      className={`tab ${active ? 'active' : ''}`}
      onClick={onClick}
    >
      {icon}
      {label}
      {count != null && (
        <span className={`tab-count ${toneRose ? 'tab-count-rose' : ''}`}>
          {count}
        </span>
      )}
    </button>
  )
}
