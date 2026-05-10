'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { I } from '../../lib/icons'
import { Modal } from '../common/Modal'
import DecisionsView from '../decisions/DecisionsView'
import { DirectionInputCard } from '../dashboard/DirectionInputCard'
import SummaryView from '../brief/SummaryView'
import WorkspaceIndex from '../workspace/WorkspaceIndex'
import { Section, DeliverableCard, RequestRow, BlockedRow } from '../brief/sections'
import { briefApi } from '../../api/brief'
import { projectsApi, type Project } from '../../api/projects'
import { decisionsApi } from '../../api/founder'
import type { BriefResponse } from '../../types/founder'

type TabId = 'direction' | 'summary' | 'decisions' | 'shipped' | 'running' | 'blocked' | 'files'

const TAB_IDS: TabId[] = ['direction', 'summary', 'decisions', 'shipped', 'running', 'blocked', 'files']

function parseTab(raw: string | null): TabId {
  if (raw && (TAB_IDS as readonly string[]).includes(raw)) return raw as TabId
  return 'summary'
}

export default function ProjectPage() {
  const t = useTranslations('nexus.project')
  const tBrief = useTranslations('nexus.brief')
  const tCommon = useTranslations('nexus.common')
  const params = useParams<{ projectId?: string | string[] }>()
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

  const tabCounts = useMemo(() => {
    if (!brief) return { shipped: null, running: null, blocked: null }
    const { shipped, running, blocked } = brief.sections
    return {
      shipped: shipped.length || null,
      running: running.length || null,
      blocked: blocked.length || null,
    }
  }, [brief])

  function setTab(id: TabId) {
    const next = new URLSearchParams(search.toString())
    if (id === 'summary') next.delete('tab')
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
          label={t('tab.direction')}
          icon={<I.Chat size={14} />}
          active={tab === 'direction'}
          onClick={() => setTab('direction')}
        />
        <TabButton
          label={t('tab.summary')}
          icon={<I.Timeline size={14} />}
          active={tab === 'summary'}
          onClick={() => setTab('summary')}
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
          label={t('tab.shipped')}
          icon={<I.Check size={14} />}
          active={tab === 'shipped'}
          onClick={() => setTab('shipped')}
          count={tabCounts.shipped}
        />
        <TabButton
          label={t('tab.running')}
          icon={<I.Zap size={14} />}
          active={tab === 'running'}
          onClick={() => setTab('running')}
          count={tabCounts.running}
        />
        <TabButton
          label={t('tab.blocked')}
          icon={<I.Alert size={14} />}
          active={tab === 'blocked'}
          onClick={() => setTab('blocked')}
          count={tabCounts.blocked}
          toneRose={(brief?.sections.blocked.length ?? 0) > 0}
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

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {tab === 'direction' && (
          <div className="project-tab-pad">
            <DirectionInputCard boundProject={project ?? null} />
          </div>
        )}
        {tab === 'summary' && <SummaryView brief={brief} />}
        {tab === 'decisions' && <DecisionsView projectId={projectId} />}
        {tab === 'shipped' && (
          <div style={{ maxWidth: 900, margin: '0 auto', padding: 16 }}>
            <Section
              count={brief?.sections.shipped.length ?? 0}
              emptyText={tBrief('section.shippedEmpty')}
            >
              {brief?.sections.shipped.map((d) => <DeliverableCard key={d.id} d={d} />)}
            </Section>
          </div>
        )}
        {tab === 'running' && (
          <div style={{ maxWidth: 900, margin: '0 auto', padding: 16 }}>
            <Section
              count={brief?.sections.running.length ?? 0}
              emptyText={tBrief('section.runningEmpty')}
            >
              {brief?.sections.running.map((r) => <RequestRow key={r.id} r={r} />)}
            </Section>
          </div>
        )}
        {tab === 'blocked' && (
          <div style={{ maxWidth: 900, margin: '0 auto', padding: 16 }}>
            <Section
              count={brief?.sections.blocked.length ?? 0}
              emptyText={tBrief('section.blockedEmpty')}
            >
              {brief?.sections.blocked.map((item) => (
                <BlockedRow key={`${item.kind}-${item.id}`} item={item} />
              ))}
            </Section>
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
