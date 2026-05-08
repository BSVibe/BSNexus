/**
 * FailureModeStrip — last-N-runs aggregate of dominant_reply_quality
 * for the project, rendered as a horizontal pill row.
 *
 * Reads ``GET /api/v1/run-summaries?aggregate=true&project_id=...&days=7``.
 * Powers the PR7 "what's regressed since last PR" baseline view. PR8
 * prompt iteration looks at this strip to know whether the iteration
 * helped (real_tool_calls↑) or hurt (pseudocode_in_chat↑).
 */

import { useTranslations } from 'next-intl'
import { useQuery } from '@tanstack/react-query'

import { runSummariesApi } from '../../api/founder'
import type { ReplyQualityKind, RunSummaryAggregate } from '../../types/founder'
import type { Tone } from '../../lib/tone'
import { Badge } from '../common/Badge'

const ORDER: ReplyQualityKind[] = [
  'real_tool_calls',
  'mixed',
  'pseudocode_in_chat',
  'fenced_block_only',
  'empty',
]

const TONE: Record<ReplyQualityKind, Tone> = {
  real_tool_calls: 'emerald',
  mixed: 'blue',
  pseudocode_in_chat: 'amber',
  fenced_block_only: 'indigo',
  empty: 'gray',
}

interface Props {
  projectId?: string
  days?: number
}

export default function FailureModeStrip({ projectId, days = 7 }: Props) {
  const t = useTranslations('nexus.inside.failureModeStrip')
  const tQuality = useTranslations('nexus.inside.runQuality.kind')
  const { data } = useQuery<RunSummaryAggregate>({
    queryKey: ['run-summary-aggregate', projectId ?? null, days],
    queryFn: () => runSummariesApi.aggregate({ projectId, days }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  if (!data) return null

  return (
    <div
      className="failure-mode-strip"
      data-testid="failure-mode-strip"
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 8,
        padding: '8px 12px',
        borderBottom: '1px solid var(--border-subtle)',
        background: 'var(--bg-base)',
      }}
    >
      <span
        className="faded"
        style={{
          fontSize: 10,
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
        }}
      >
        {t('title')}
      </span>
      {data.total_runs === 0 ? (
        <span className="faded" style={{ fontSize: 12 }}>
          {t('noRuns')}
        </span>
      ) : (
        <>
          {ORDER.map((kind) => {
            const count = data.counts[kind] ?? 0
            if (count === 0) return null
            return (
              <span
                key={kind}
                style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}
                data-testid={`fms-${kind}`}
              >
                <Badge tone={TONE[kind]}>{tQuality(kind as never)}</Badge>
                <span className="mono" style={{ fontSize: 11 }}>
                  {count}
                </span>
              </span>
            )
          })}
          <span className="faded mono" style={{ fontSize: 11 }}>
            · {data.total_runs} {t('totalSuffix')}
          </span>
        </>
      )}
    </div>
  )
}
