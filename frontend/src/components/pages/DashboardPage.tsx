'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useRouter, useSearchParams } from 'next/navigation'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge, StatusDot } from '../common/Badge'
import { DecisionInboxStrip } from '../dashboard/DecisionInboxStrip'
import { DirectionInputCard } from '../dashboard/DirectionInputCard'
import { I } from '../../lib/icons'
import { relTime, truncId } from '../../lib/fmt'
import { statusTone, type Tone } from '../../lib/tone'
import { projectsApi, type Project } from '../../api/projects'
import { requestsApi, deliverablesApi, decisionsApi } from '../../api/founder'
import type { Decision, Deliverable, Request as FounderRequest } from '../../types/founder'

const STATUS_FILTERS = ['all', 'active', 'archived'] as const
const DASHBOARD_AGGREGATE_PROJECT_LIMIT = 8
type StatusFilter = (typeof STATUS_FILTERS)[number]

export default function DashboardPage() {
  const queryClient = useQueryClient()
  const router = useRouter()
  const search = useSearchParams()
  const t = useTranslations('nexus.dashboard')
  const tCommon = useTranslations('nexus.common')
  const newParam = search.get('new') === '1'
  const [createOpen, setCreateOpen] = useState(newParam)
  const [filter, setFilter] = useState<StatusFilter>('all')

  // When the caller navigates to ``?new=1`` while the page is already
  // mounted (e.g. from the command palette), open the modal. Using the
  // during-render previous-value pattern avoids a setState-in-effect.
  const [prevNewParam, setPrevNewParam] = useState(newParam)
  if (prevNewParam !== newParam) {
    setPrevNewParam(newParam)
    if (newParam && !createOpen) setCreateOpen(true)
  }

  const { data: projects = [], isLoading } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  const aggregateProjects = useMemo(
    () => projects.slice(0, DASHBOARD_AGGREGATE_PROJECT_LIMIT),
    [projects],
  )

  // Aggregate a bounded set of recent projects. Fetching every project's
  // child resources on page load trips API rate limits as workspaces grow.
  const reqQueries = useQueries({
    queries: aggregateProjects.map((p) => ({
      queryKey: ['requests', p.id],
      queryFn: () => requestsApi.listForProject(p.id),
    })),
  })
  const delQueries = useQueries({
    queries: aggregateProjects.map((p) => ({
      queryKey: ['deliverables', p.id],
      queryFn: () => deliverablesApi.listForProject(p.id),
    })),
  })
  const decQueries = useQueries({
    queries: aggregateProjects.map((p) => ({
      queryKey: ['decisions', p.id],
      queryFn: () => decisionsApi.listForProject(p.id),
    })),
  })

  const allRequests = useMemo(
    () => reqQueries.flatMap((q) => q.data ?? []),
    [reqQueries],
  )
  const allDeliverables = useMemo(
    () => delQueries.flatMap((q) => q.data ?? []),
    [delQueries],
  )
  const allDecisions = useMemo(
    () => decQueries.flatMap((q) => q.data ?? []),
    [decQueries],
  )

  const openDecisions = allDecisions.filter((d) => !d.resolved_at)
  const blocking = openDecisions.filter((d) => d.blocking)
  const activeRequests = allRequests.filter(
    (r) => r.status === 'running' || r.status === 'open',
  )
  // Lazy-initialised once at mount so render stays pure. The dashboard
  // is a snapshot — the cutoff doesn't need to drift while the user
  // looks at it.
  const [sevenDaysAgo] = useState(() => Date.now() - 7 * 86400 * 1000)
  const shipped7d = allDeliverables.filter(
    (d) =>
      d.status === 'delivered' && new Date(d.created_at).getTime() >= sevenDaysAgo,
  )

  const filteredProjects = useMemo(() => {
    if (filter === 'all') return projects
    return projects.filter((p) => p.status === filter)
  }, [projects, filter])

  const perProject = useMemo(() => {
    const byProject = new Map<
      string,
      { requests: FounderRequest[]; decisions: Decision[]; deliverables: Deliverable[] }
    >()
    for (const p of projects) {
      byProject.set(p.id, {
        requests: allRequests.filter((r) => r.project_id === p.id),
        decisions: allDecisions.filter((d) => d.project_id === p.id),
        deliverables: allDeliverables.filter((d) => d.project_id === p.id),
      })
    }
    return byProject
  }, [projects, allRequests, allDecisions, allDeliverables])

  const [createName, setCreateName] = useState('')
  const [createDesc, setCreateDesc] = useState('')

  const createMutation = useMutation({
    mutationFn: () =>
      projectsApi.create({
        name: createName.trim(),
        description: createDesc.trim(),
      }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setCreateOpen(false)
      setCreateName('')
      setCreateDesc('')
      const next = new URLSearchParams(search.toString())
      next.delete('new')
      const qs = next.toString()
      router.replace(qs ? `/dashboard?${qs}` : '/dashboard')
      router.push(`/projects/${project.id}`)
    },
  })

  return (
    <div className="page fade-in">
      <div className="page-hd" style={{ justifyContent: 'flex-end' }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() =>
              queryClient.invalidateQueries({ queryKey: ['projects'] })
            }
          >
            <I.Refresh size={14} /> {tCommon('refresh')}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setCreateOpen(true)}
          >
            <I.Plus size={14} /> {t('newProject')}
          </button>
        </div>
      </div>

      {/* Home Decision Inbox strip — locked first slot in core-ux-spec
          §Default Home Layout (decision-locks A2 / A3). */}
      <DecisionInboxStrip />

      {/* G7 — Direction input. Founder primitive: short directive opens
          a Request via POST /api/v1/directions. Sits below the Inbox so
          the locked priority slot is preserved. */}
      <DirectionInputCard />

      {/* aggregate strip */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
          marginBottom: 24,
        }}
      >
        <Stat
          label={t('stat.activeProjects')}
          value={projects.filter((p) => p.status === 'active').length}
          sub={t('stat.activeProjectsSub', { total: projects.length })}
          tone="blue"
        />
        <Stat
          label={t('stat.blockingDecisions')}
          value={blocking.length}
          sub={t('stat.blockingDecisionsSub', { open: openDecisions.length })}
          tone={blocking.length > 0 ? 'rose' : 'gray'}
          onClick={() => {
            if (blocking[0]) router.push(`/projects/${blocking[0].project_id}`)
          }}
        />
        <Stat
          label={t('stat.activeRequests')}
          value={activeRequests.length}
          sub={t('stat.activeRequestsSub', { total: allRequests.length })}
          tone="emerald"
        />
        <Stat
          label={t('stat.deliveredWindow')}
          value={shipped7d.length}
          sub={t('stat.deliveredWindowSub')}
          tone="amber"
        />
      </div>

      {/* filter chips */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 12,
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--gray-100)' }}>
          {t('projectsHeading')}
        </span>
        <span style={{ flex: 1 }} />
        {STATUS_FILTERS.map((s) => (
          <button
            key={s}
            type="button"
            className={`btn btn-sm ${filter === s ? 'btn-secondary' : 'btn-ghost'}`}
            onClick={() => setFilter(s)}
          >
            {t(`filter.${s}`)}
            <span className="mono faded" style={{ fontSize: 10, marginLeft: 4 }}>
              {s === 'all' ? projects.length : projects.filter((p) => p.status === s).length}
            </span>
          </button>
        ))}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill,minmax(340px,1fr))',
          gap: 12,
        }}
      >
        {filteredProjects.map((p) => {
          const bundle = perProject.get(p.id)
          return (
            <ProjectCard
              key={p.id}
              project={p}
              activeReqCount={
                bundle?.requests.filter(
                  (r) => r.status === 'open' || r.status === 'running',
                ).length ?? 0
              }
              openDecisions={
                bundle?.decisions.filter((d) => !d.resolved_at).length ?? 0
              }
              deliveredThisWeek={
                bundle?.deliverables.filter(
                  (d) =>
                    d.status === 'delivered' &&
                    new Date(d.created_at).getTime() >= sevenDaysAgo,
                ).length ?? 0
              }
              onOpen={() => router.push(`/projects/${p.id}`)}
            />
          )
        })}
        {!isLoading && filteredProjects.length === 0 && (
          <div
            className="card"
            style={{ gridColumn: '1/-1', padding: 48, textAlign: 'center' }}
          >
            <div style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>
              {projects.length === 0
                ? t('empty.noProjects')
                : t('empty.noFilterMatch')}
            </div>
          </div>
        )}
      </div>

      {createOpen && (
        <CreateProjectModal
          name={createName}
          desc={createDesc}
          onChangeName={setCreateName}
          onChangeDesc={setCreateDesc}
          onClose={() => {
            setCreateOpen(false)
            const next = new URLSearchParams(search.toString())
            next.delete('new')
            const qs = next.toString()
            router.replace(qs ? `/dashboard?${qs}` : '/dashboard')
          }}
          onSubmit={() => createMutation.mutate()}
          pending={createMutation.isPending}
          fallbackMentionToken={t('newProject')}
        />
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  sub,
  tone,
  onClick,
}: {
  label: string
  value: number
  sub: string
  tone: Tone
  onClick?: () => void
}) {
  return (
    <button
      type="button"
      className="card"
      style={{
        padding: 16,
        cursor: onClick ? 'pointer' : 'default',
        textAlign: 'left',
        transition: 'border var(--t-fast) var(--ease)',
      }}
      onClick={onClick}
      disabled={!onClick}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 11,
          color: 'var(--text-tertiary)',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
          marginBottom: 8,
        }}
      >
        <StatusDot tone={tone} size={8} />
        {label}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <div
          style={{
            fontSize: 30,
            lineHeight: '32px',
            fontWeight: 700,
            color: 'var(--gray-50)',
            letterSpacing: '-0.02em',
            fontFamily: 'var(--font-mono)',
          }}
        >
          {value}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{sub}</div>
      </div>
    </button>
  )
}

function ProjectCard({
  project,
  activeReqCount,
  openDecisions,
  deliveredThisWeek,
  onOpen,
}: {
  project: Project
  activeReqCount: number
  openDecisions: number
  deliveredThisWeek: number
  onOpen: () => void
}) {
  const tCard = useTranslations('nexus.dashboard.card')
  const tStatus = useTranslations('nexus.status')
  const projectStatusLabel = (() => {
    const s = project.status
    if (s === 'active') return tStatus('active')
    if (s === 'archived') return tStatus('archived')
    return s
  })()
  return (
    <button
      type="button"
      className="card"
      style={{
        padding: 16,
        cursor: 'pointer',
        textAlign: 'left',
        transition: 'all var(--t-fast) var(--ease)',
      }}
      onClick={onOpen}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 8,
          marginBottom: 8,
        }}
      >
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 2,
            }}
          >
            <Badge tone={statusTone(project.status)} dot>
              {projectStatusLabel}
            </Badge>
            <span
              className="mono faded"
              style={{ fontSize: 11 }}
              title={project.id}
            >
              {truncId(project.id)}
            </span>
          </div>
          <div
            style={{
              fontSize: 15,
              fontWeight: 600,
              color: 'var(--gray-50)',
              marginBottom: 4,
              lineHeight: '20px',
            }}
          >
            {project.name}
          </div>
          {project.description && (
            <div
              style={{
                fontSize: 12,
                color: 'var(--text-secondary)',
                lineHeight: '18px',
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {project.description}
            </div>
          )}
        </div>
      </div>

      <div
        style={{
          height: 1,
          background: 'var(--border-subtle)',
          margin: '12px 0',
        }}
      />

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          fontSize: 11,
          color: 'var(--text-tertiary)',
        }}
      >
        <span>
          <span className="hl mono" style={{ fontSize: 12 }}>
            {activeReqCount}
          </span>{' '}
          {tCard('requests')}
        </span>
        <span>
          <span className="hl mono" style={{ fontSize: 12 }}>
            {openDecisions}
          </span>{' '}
          {tCard('pending')}
        </span>
        <span>
          <span className="hl mono" style={{ fontSize: 12 }}>
            {deliveredThisWeek}
          </span>{' '}
          {tCard('shipped')}
        </span>
        <span style={{ flex: 1 }} />
        <span title={new Date(project.updated_at).toLocaleString()}>
          {relTime(project.updated_at)}
        </span>
      </div>
    </button>
  )
}

function CreateProjectModal({
  name,
  desc,
  onChangeName,
  onChangeDesc,
  onClose,
  onSubmit,
  pending,
  fallbackMentionToken,
}: {
  name: string
  desc: string
  onChangeName: (v: string) => void
  onChangeDesc: (v: string) => void
  onClose: () => void
  onSubmit: () => void
  pending: boolean
  fallbackMentionToken: string
}) {
  const tModal = useTranslations('nexus.dashboard.createModal')
  const tCommon = useTranslations('nexus.common')
  // Translate the bracketed ``mentionToken`` into a styled inline span
  // so the placeholder ``@`` chip keeps its monospace highlighting.
  // We render via ``t.rich`` to preserve the JSX while letting locales
  // re-order the surrounding text.
  return (
    <div className="cmd-mask" onClick={onClose}>
      <div className="cmd" style={{ width: 520 }} onClick={(e) => e.stopPropagation()}>
        <div
          style={{
            padding: '14px 16px',
            borderBottom: '1px solid var(--border-subtle)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>{tModal('title')}</div>
          <button type="button" className="btn btn-icon" onClick={onClose}>
            <I.X size={14} />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (name.trim()) onSubmit()
          }}
          style={{
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          <div>
            <label
              style={{
                fontSize: 12,
                color: 'var(--text-secondary)',
                marginBottom: 4,
                display: 'block',
              }}
            >
              {tModal('nameLabel')}
            </label>
            <input
              className="input"
              value={name}
              autoFocus
              onChange={(e) => onChangeName(e.target.value)}
              placeholder={tModal('namePlaceholder')}
            />
          </div>
          <div>
            <label
              style={{
                fontSize: 12,
                color: 'var(--text-secondary)',
                marginBottom: 4,
                display: 'block',
              }}
            >
              {tModal('descriptionLabel')}
            </label>
            <input
              className="input"
              value={desc}
              onChange={(e) => onChangeDesc(e.target.value)}
              placeholder={tModal('descriptionPlaceholder')}
            />
          </div>
          <div
            style={{
              fontSize: 12,
              color: 'var(--text-tertiary)',
              padding: '8px 12px',
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--r-md)',
            }}
          >
            <I.Sparkle
              size={12}
              style={{ display: 'inline', marginRight: 6, verticalAlign: -2 }}
            />{' '}
            {tModal('hint', {
              mentionToken: `@${name || fallbackMentionToken}`,
            })}
          </div>
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 8,
              paddingTop: 8,
              borderTop: '1px solid var(--border-subtle)',
            }}
          >
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              {tCommon('cancel')}
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!name.trim() || pending}
            >
              {pending ? tCommon('creating') : tModal('submit')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
