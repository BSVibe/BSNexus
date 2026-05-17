'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { I } from '../../lib/icons'
import { Modal } from '../common/Modal'
import DecisionsView from '../decisions/DecisionsView'
import { DirectionInputCard } from '../dashboard/DirectionInputCard'
import HomeView from '../brief/HomeView'
import RepoConfigModal from '../settings/RepoConfigModal'
import WorkspaceIndex from '../workspace/WorkspaceIndex'
import {
  TableSection,
  DeliverableCard,
  RequestRow,
  BlockedRow,
  useRequestColumns,
  useDeliverableColumns,
  useBlockedColumns,
} from '../brief/sections'
import { briefApi } from '../../api/brief'
import { projectsApi, type Project } from '../../api/projects'
import { decisionsApi } from '../../api/founder'
import type { BriefResponse } from '../../types/founder'

type TabId = 'home' | 'decisions' | 'work' | 'files'

const TAB_IDS: TabId[] = ['home', 'decisions', 'work', 'files']

function parseTab(raw: string | null): TabId {
  if (raw && (TAB_IDS as readonly string[]).includes(raw)) return raw as TabId
  return 'home'
}

/**
 * ProjectPage — 4-tab founder surface (G7.5e).
 *
 * Consolidated from the G7.5d 7-tab layout (지시 / 요약 / 의사결정 /
 * 납품 / 진행 / 막힘 / 파일) — the seven shorter sections were each
 * too thin individually and crowded the tab bar on mobile.
 *
 *   - 홈        : DirectionInputCard + summary counts + "다음" list
 *   - 결정      : blocking decisions ∪ verification_failed deliverables ∪ blocked requests
 *   - 작업      : running requests + verified shipped deliverables
 *   - 파일      : WorkspaceIndex
 */
export default function ProjectPage() {
  const t = useTranslations('nexus.project')
  const tBrief = useTranslations('nexus.brief')
  const tCommon = useTranslations('nexus.common')
  const requestColumns = useRequestColumns()
  const deliverableColumns = useDeliverableColumns()
  const blockedColumns = useBlockedColumns()
  const params = useParams<{ projectId?: string | string[] }>()
  const rawProjectId = params?.projectId
  const projectId = Array.isArray(rawProjectId) ? rawProjectId[0] : rawProjectId
  const search = useSearchParams()
  const tab = parseTab(search.get('tab'))
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [overflowOpen, setOverflowOpen] = useState(false)
  const [repoConfigOpen, setRepoConfigOpen] = useState(false)
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

  const { data: brief } = useQuery<BriefResponse>({
    queryKey: ['brief', projectId],
    queryFn: () => briefApi.forProject(projectId!, { limit: 10 }),
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

  // Counts for the merged tabs.
  // 결정 = open decisions + blocked (verification_failed deliverables + blocked requests).
  // 작업 = running requests + shipped deliverables.
  const tabCounts = useMemo(() => {
    const blockedCount = brief?.sections.blocked.length ?? 0
    const runningCount = brief?.sections.running.length ?? 0
    const shippedCount = brief?.sections.shipped.length ?? 0
    return {
      decisions: openDecisions + blockedCount,
      decisionsRose: openDecisions > 0 || blockedCount > 0,
      work: runningCount + shippedCount,
    }
  }, [brief, openDecisions])

  function setTab(id: TabId) {
    const next = new URLSearchParams(search.toString())
    if (id === 'home') next.delete('tab')
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
      <div
        className="tabs"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 0,
          overflowX: 'auto',
          scrollbarWidth: 'none',
        }}
      >
        <TabButton
          label={t('tab.home')}
          icon={<I.Home size={14} />}
          active={tab === 'home'}
          onClick={() => setTab('home')}
        />
        <TabButton
          label={t('tab.decisions')}
          icon={<I.Inbox size={14} />}
          active={tab === 'decisions'}
          onClick={() => setTab('decisions')}
          count={tabCounts.decisions || null}
          toneRose={tabCounts.decisionsRose}
        />
        <TabButton
          label={t('tab.work')}
          icon={<I.Zap size={14} />}
          active={tab === 'work'}
          onClick={() => setTab('work')}
          count={tabCounts.work || null}
        />
        <TabButton
          label={t('tab.files')}
          icon={<I.Doc size={14} />}
          active={tab === 'files'}
          onClick={() => setTab('files')}
        />
        <span style={{ flex: 1, minWidth: 12 }} />
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
                  minHeight: 44,
                }}
                onClick={() => {
                  setOverflowOpen(false)
                  setRepoConfigOpen(true)
                }}
              >
                <I.Doc size={14} />
                <span style={{ marginLeft: 6 }}>{t('repoConfig.menuItem')}</span>
              </button>
              <button
                type="button"
                role="menuitem"
                className="btn btn-ghost"
                style={{
                  width: '100%',
                  justifyContent: 'flex-start',
                  color: 'var(--rose-500)',
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
              style={{ background: 'var(--rose-500)', borderColor: 'var(--rose-500)' }}
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
          <p style={{ color: 'var(--rose-500)', marginTop: 12, fontSize: 13 }}>
            {t('deleteModal.errorPrefix')} {(deleteMutation.error as Error)?.message}
          </p>
        )}
      </Modal>

      {projectId && (
        <RepoConfigModal
          open={repoConfigOpen}
          onClose={() => setRepoConfigOpen(false)}
          projectId={projectId}
        />
      )}

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {tab === 'home' && (
          <div style={{ maxWidth: 900, margin: '0 auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 20 }}>
            <DirectionInputCard boundProject={project ?? null} />
            <HomeView brief={brief} openDecisions={openDecisions} />
          </div>
        )}
        {tab === 'decisions' && (
          <div style={{ maxWidth: 900, margin: '0 auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 24 }}>
            <DecisionsView projectId={projectId} />
            {(brief?.sections.blocked.length ?? 0) > 0 && (
              <TableSection
                title={tBrief('section.blocked')}
                count={brief?.sections.blocked.length ?? 0}
                emptyText={tBrief('section.blockedEmpty')}
                columns={blockedColumns}
                rows={brief?.sections.blocked ?? []}
                rowKey={(item) => `${item.kind}-${item.id}`}
                renderMobileCard={(item) => <BlockedRow item={item} />}
              />
            )}
          </div>
        )}
        {tab === 'work' && (
          <div style={{ maxWidth: 900, margin: '0 auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 24 }}>
            <TableSection
              title={tBrief('section.running')}
              count={brief?.sections.running.length ?? 0}
              emptyText={tBrief('section.runningEmpty')}
              columns={requestColumns}
              rows={brief?.sections.running ?? []}
              rowKey={(r) => r.id}
              renderMobileCard={(r) => <RequestRow r={r} />}
            />
            <TableSection
              title={tBrief('section.shipped')}
              count={brief?.sections.shipped.length ?? 0}
              emptyText={tBrief('section.shippedEmpty')}
              columns={deliverableColumns}
              rows={brief?.sections.shipped ?? []}
              rowKey={(d) => d.id}
              renderMobileCard={(d) => <DeliverableCard d={d} />}
            />
          </div>
        )}
        {tab === 'files' && (
          <WorkspaceIndex
            projectId={projectId}
            initialPath={search.get('path')}
          />
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
      style={{ flex: '0 0 auto', whiteSpace: 'nowrap' }}
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
