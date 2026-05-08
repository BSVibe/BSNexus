/**
 * RunActivityTimeline — chronological activity log for one ExecutionRun.
 *
 * Tool-level activities (file_write start/done, etc.) collapse behind
 * a "Show tool log (N)" toggle since they're noisy. Milestones (state
 * transitions, llm_round_complete) always visible.
 */

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { useQuery } from '@tanstack/react-query'

import { requestsApi } from '../../api/founder'
import type { RunActivity } from '../../types/founder'

interface Props {
  runId: string
}

export default function RunActivityTimeline({ runId }: Props) {
  const t = useTranslations('nexus.inside.activity')
  const [showTool, setShowTool] = useState(false)
  const { data: activities = [], isLoading } = useQuery<RunActivity[]>({
    queryKey: ['run-activities', runId],
    queryFn: () => requestsApi.listActivities(runId),
    refetchInterval: 4000,
  })

  if (isLoading) {
    return (
      <div className="faded" style={{ fontSize: 12 }}>
        {t('loading')}
      </div>
    )
  }
  if (activities.length === 0) {
    return (
      <div className="faded" style={{ fontSize: 12 }}>
        {t('empty')}
      </div>
    )
  }

  const milestones = activities.filter((a) => a.level === 'milestone')
  const toolRows = activities.filter((a) => a.level === 'tool')

  return (
    <div className="run-activity-timeline" data-testid="run-activity-timeline">
      <ul role="list" style={{ margin: 0, padding: 0, listStyle: 'none' }}>
        {milestones.map((row) => (
          <ActivityRow key={row.id} activity={row} />
        ))}
      </ul>
      {toolRows.length > 0 && (
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          style={{ marginTop: 8 }}
          onClick={() => setShowTool((v) => !v)}
          data-testid="toggle-tool-log"
        >
          {showTool ? t('hideToolLog') : t('showToolLog', { count: toolRows.length })}
        </button>
      )}
      {showTool && (
        <ul
          role="list"
          aria-label={t('toolLogLabel')}
          style={{ margin: '8px 0 0', padding: 0, listStyle: 'none' }}
        >
          {toolRows.map((row) => (
            <ActivityRow key={row.id} activity={row} compact />
          ))}
        </ul>
      )}
    </div>
  )
}

function ActivityRow({ activity, compact }: { activity: RunActivity; compact?: boolean }) {
  return (
    <li
      style={{
        display: 'flex',
        gap: 8,
        padding: compact ? '4px 0' : '6px 0',
        borderBottom: '1px solid var(--border-subtle)',
        fontSize: compact ? 11 : 12,
        color: compact ? 'var(--gray-300)' : 'var(--gray-200)',
      }}
    >
      <span
        className="mono faded"
        style={{ fontSize: 10, minWidth: 56 }}
        title={activity.created_at}
      >
        {formatTime(activity.created_at)}
      </span>
      <span style={{ flex: 1, fontFamily: compact ? 'var(--font-mono)' : undefined }}>
        {activity.summary}
      </span>
    </li>
  )
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return iso.slice(11, 19)
  }
}
