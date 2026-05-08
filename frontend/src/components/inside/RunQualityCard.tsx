/**
 * RunQualityCard — at-a-glance failure-mode summary for one ExecutionRun.
 *
 * Reads the run.run_summary aggregate produced by PR7 backend
 * instrumentation. Empty/null summary renders a compact placeholder
 * (the run hasn't reached terminal state yet, or the BSGateway path
 * doesn't populate it).
 */

import { useTranslations } from 'next-intl'

import type { ReplyQualityKind, RunSummary } from '../../types/founder'
import type { Tone } from '../../lib/tone'
import { Badge } from '../common/Badge'

const QUALITY_TONE: Record<ReplyQualityKind, Tone> = {
  real_tool_calls: 'emerald',
  pseudocode_in_chat: 'amber',
  fenced_block_only: 'indigo',
  empty: 'gray',
  mixed: 'blue',
}

interface Props {
  summary: RunSummary | null
}

export default function RunQualityCard({ summary }: Props) {
  const t = useTranslations('nexus.inside.runQuality')
  if (!summary) {
    return (
      <div className="run-quality-card run-quality-card--empty" style={containerStyle}>
        <span className="faded" style={{ fontSize: 12 }}>
          {t('notYet')}
        </span>
      </div>
    )
  }
  const tone = QUALITY_TONE[summary.dominant_reply_quality] ?? 'gray'
  return (
    <div
      className="run-quality-card"
      style={containerStyle}
      data-testid="run-quality-card"
      data-quality={summary.dominant_reply_quality}
    >
      <Badge tone={tone}>{t(`kind.${summary.dominant_reply_quality}` as never)}</Badge>
      <Pill label={t('rounds')} value={String(summary.total_rounds)} />
      <Pill label={t('toolCalls')} value={String(summary.total_tool_calls)} />
      <Pill
        label={t('files')}
        value={String(summary.files_actually_written.length)}
        title={summary.files_actually_written.join('\n') || undefined}
      />
      {summary.did_emit_fenced_block && (
        <Badge tone="indigo">{t('fencedBlock')}</Badge>
      )}
      {summary.failure_signals.length > 0 && (
        <span
          className="run-quality-card__signal"
          style={{
            fontSize: 11,
            color: 'var(--text-tertiary)',
            flexBasis: '100%',
            marginTop: 6,
          }}
          title={summary.failure_signals.join('\n')}
        >
          ⚠ {summary.failure_signals[0]}
        </span>
      )}
    </div>
  )
}

const containerStyle: React.CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: 6,
  alignItems: 'center',
  padding: '8px 10px',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-md)',
}

function Pill({
  label,
  value,
  title,
}: {
  label: string
  value: string
  title?: string
}) {
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex',
        gap: 4,
        alignItems: 'baseline',
        fontSize: 11,
        color: 'var(--gray-200)',
        padding: '2px 6px',
      }}
    >
      <span className="faded" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {label}
      </span>
      <span className="mono">{value}</span>
    </span>
  )
}
