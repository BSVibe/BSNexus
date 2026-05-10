'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { I } from '../../lib/icons'
import { Modal } from '../common/Modal'
import BriefView from '../brief/BriefView'
import DecisionsView from '../decisions/DecisionsView'
import { DirectionInputCard } from '../dashboard/DirectionInputCard'
import { projectsApi, type Project } from '../../api/projects'
import { decisionsApi, deliverablesApi } from '../../api/founder'

type TabId = 'brief' | 'decisions'

function parseTab(raw: string | null): TabId {
  if (raw === 'decisions') return raw
  return 'brief'
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
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [overflowOpen, setOverflowOpen] = useState(false)
  const router = useRouter()
  const queryClient = useQueryClient()
  const overflowRef = useRef<HTMLDivElement | null>(null)

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
  })

  const { data: decisions = [] } = useQuery({
    queryKey: ['decisions', projectId],
    queryFn: () => decisionsApi.listForProject(projectId!),
    enabled: Boolean(projectId),
  })

  const openDecisions = useMemo(
    () => decisions.filter((d) => !d.resolved_at).length,
    [decisions],
  )

  function setTab(id: TabId) {
    const next = new URLSearchParams(search.toString())
    if (id === 'brief') next.delete('tab')
    else next.set('tab', id)
    router.replace(buildProjectUrl(next))
  }

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
          label={t('tab.brief')}
          icon={<I.Timeline size={14} />}
          active={tab === 'brief'}
          onClick={() => setTab('brief')}
          count={deliverables.length || null}
        />
        <TabButton
          label={t('tab.decisions')}
          icon={<I.Inbox size={14} />}
          active={tab === 'decisions'}
          onClick={() => setTab('decisions')}
          count={openDecisions || null}
          toneRose={openDecisions > 0}
        />
        <span style={{ flex: 1 }} />
        {/* G7.1 — destructive actions live in the header overflow menu,
            not the tab strip. Tabs are view switches; delete is an
            action on the project itself. */}
        <div ref={overflowRef} style={{ position: 'relative', marginRight: 8 }}>
          <button
            type="button"
            className="btn btn-icon"
            title={t('headerOverflowAria')}
            aria-label={t('headerOverflowAria')}
            aria-haspopup="menu"
            aria-expanded={overflowOpen}
            onClick={() => setOverflowOpen((open) => !open)}
          >
            <I.Ellipsis size={14} />
          </button>
          {overflowOpen && (
            <div
              role="menu"
              className="card"
              style={{
                position: 'absolute',
                top: 'calc(100% + 4px)',
                right: 0,
                minWidth: 200,
                padding: 6,
                zIndex: 30,
                boxShadow: 'var(--sh-md)',
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <button
                type="button"
                role="menuitem"
                className="btn btn-ghost"
                style={{
                  width: '100%',
                  justifyContent: 'flex-start',
                  color: 'var(--color-rose)',
                  minHeight: 44,
                }}
                onClick={() => {
                  setOverflowOpen(false)
                  setConfirmDelete(true)
                }}
              >
                <I.Trash size={14} />
                <span style={{ marginLeft: 6 }}>{t('deleteModal.submit')}</span>
              </button>
            </div>
          )}
        </div>
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

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {tab === 'brief' && (
          <div className="project-tab-pad">
            <DirectionInputCard boundProject={project ?? null} />
            <BriefView projectId={projectId} />
          </div>
        )}
        {tab === 'decisions' && <DecisionsView projectId={projectId} />}
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
