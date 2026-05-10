'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { I } from '../../lib/icons'
import { directionsApi } from '../../api/directions'
import { projectsApi, type Project } from '../../api/projects'
import type { DirectionAckResponse, DirectionRoutingPrompt } from '../../types/founder'

/**
 * DirectionInputCard — greenfield Direction primitive (G7 #92).
 *
 * Founder types a short directive ("Add a /healthz endpoint") and the
 * server (POST /api/v1/directions, G1 from PR #86) opens a Request when
 * the project can be identified. When the project is ambiguous the
 * server returns a routing prompt; the card switches to a project
 * picker so the founder can disambiguate without leaving the surface.
 *
 * Two surfaces use this card:
 * - Dashboard — no ``boundProject``; the server picks among all
 *   tenant projects, optionally returning a routing prompt.
 * - ProjectPage Brief tab — passes ``boundProject``; the directive
 *   is implicitly scoped to that project, replacing the legacy
 *   chat-rail ``GlobalChat`` surface that retired with conversation.ts.
 *
 * Touch-target floor: submit button + routing option buttons keep the
 * 44 × 44 CSS pixel WCAG 2.5.5 AAA / Apple HIG minimum on mobile.
 */
export interface DirectionInputCardProps {
  boundProject?: Project | null
}

export function DirectionInputCard({ boundProject = null }: DirectionInputCardProps = {}) {
  const t = useTranslations('nexus.dashboard.direction')
  const router = useRouter()
  const queryClient = useQueryClient()

  const [body, setBody] = useState('')
  const [routing, setRouting] = useState<DirectionRoutingPrompt | null>(null)
  const [pendingBody, setPendingBody] = useState<string | null>(null)
  const [ack, setAck] = useState<string | null>(null)

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    enabled: boundProject == null,
  })

  const mutation = useMutation({
    mutationFn: async (args: { body: string; project_id?: string | null }): Promise<DirectionAckResponse> =>
      directionsApi.create({
        body: args.body,
        source: 'mobile_web',
        project_id: args.project_id ?? null,
      }),
    onSuccess: (resp) => {
      if (resp.routing) {
        setRouting(resp.routing)
        return
      }
      // Direction routed to a request — clear the input + show ack
      // copy in the active locale. Backend no longer sends ack strings
      // (G7.1); the wire shape is state-only.
      queryClient.invalidateQueries({ queryKey: ['requests'] })
      queryClient.invalidateQueries({ queryKey: ['brief'] })
      setBody('')
      setRouting(null)
      setPendingBody(null)
      setAck(t('ackSuccess'))
      const projectId = resp.request?.project_id ?? resp.direction.project_id
      if (projectId) {
        router.prefetch(`/projects/${projectId}`)
      }
    },
  })

  const projectsAvailable = projects.length

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = body.trim()
    if (!trimmed) return
    setAck(null)
    setPendingBody(trimmed)
    // Bound surface (ProjectPage) — pre-bind the active project so the
    // server skips the routing prompt. Free surface (Dashboard) — only
    // pre-bind when the founder has exactly one project, otherwise let
    // the server decide between routing prompt and implicit selection.
    const projectId = boundProject?.id
      ?? (projectsAvailable === 1 ? projects[0].id : null)
    mutation.mutate({ body: trimmed, project_id: projectId })
  }

  const onPickProject = (projectId: string) => {
    if (!pendingBody) return
    mutation.mutate({ body: pendingBody, project_id: projectId })
  }

  const submitDisabled = body.trim().length === 0 || mutation.isPending

  return (
    <section
      className="card"
      data-testid="direction-input-card"
      style={{ padding: 16, marginBottom: 24 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <I.Sparkle size={14} />
        <h2 style={{ fontSize: 14, fontWeight: 600, color: 'var(--gray-50)', margin: 0, flex: 1 }}>
          {t('heading')}
        </h2>
      </div>

      <form onSubmit={onSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <textarea
          className="input"
          aria-label={t('textareaLabel')}
          placeholder={t('placeholder')}
          rows={3}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          disabled={mutation.isPending}
          style={{
            width: '100%',
            resize: 'vertical',
            minHeight: 88,
            fontSize: 14,
            lineHeight: '20px',
            fontFamily: 'inherit',
          }}
        />

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="faded" style={{ fontSize: 11, flex: 1 }}>
            {t('hint')}
          </span>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitDisabled}
            style={{ minHeight: 44, minWidth: 120, justifyContent: 'center' }}
          >
            {mutation.isPending ? t('submitting') : t('submit')}
          </button>
        </div>

        {mutation.isError && (
          <div
            role="alert"
            style={{
              fontSize: 12,
              color: 'var(--color-rose)',
              padding: '8px 12px',
              background: 'var(--bg-elevated)',
              border: '1px solid var(--color-rose)',
              borderRadius: 'var(--r-md)',
            }}
          >
            {t('error')}
          </div>
        )}

        {ack && !routing && (
          <div
            role="status"
            data-testid="direction-ack"
            style={{
              fontSize: 12,
              color: 'var(--gray-200)',
              padding: '8px 12px',
              background: 'var(--bg-elevated)',
              borderRadius: 'var(--r-md)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            {ack}
          </div>
        )}

        {routing && (
          <div
            data-testid="direction-routing-prompt"
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              padding: 12,
              background: 'var(--bg-elevated)',
              borderRadius: 'var(--r-md)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--gray-100)' }}>
              {routing.question}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {routing.options.map((opt) => (
                <button
                  key={opt.project_id}
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => onPickProject(opt.project_id)}
                  disabled={mutation.isPending}
                  style={{ minHeight: 44, justifyContent: 'flex-start' }}
                >
                  {opt.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </form>
    </section>
  )
}
