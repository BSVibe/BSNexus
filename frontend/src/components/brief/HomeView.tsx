'use client'

import { useTranslations } from 'next-intl'

import { Section } from './sections'
import type { BriefResponse } from '../../types/founder'

/**
 * HomeView — top-level project overview that lives under the
 * DirectionInputCard in the 홈 tab (G7.5e).
 *
 * G7.5e collapses the previous "지시" + "요약" tabs into a single 홈
 * surface, with the input card on top and this view's roll-up below.
 * Counts here mirror the new 4-tab structure:
 *   - 결정 = open blocking decisions ∪ verification_failed deliverables ∪ blocked requests
 *   - 작업 = running requests + verified shipped deliverables
 */
export default function HomeView({
  brief,
  openDecisions,
}: {
  brief: BriefResponse | null | undefined
  openDecisions: number
}) {
  const t = useTranslations('nexus.project.home')
  const tBrief = useTranslations('nexus.brief')

  if (!brief) {
    return (
      <div style={{ padding: 8, color: 'var(--text-tertiary)', fontSize: 13 }}>
        {tBrief('loading')}
      </div>
    )
  }

  const { shipped, blocked, running, next } = brief.sections
  const decisionsCount = openDecisions + blocked.length
  const workCount = running.length + shipped.length

  const counts: {
    key: string
    label: string
    count: number
    tone?: 'rose' | 'blue' | 'amber' | 'emerald'
  }[] = [
    {
      key: 'decisions',
      label: t('decisionsLabel'),
      count: decisionsCount,
      tone: decisionsCount > 0 ? 'rose' : undefined,
    },
    {
      key: 'work',
      label: t('workLabel'),
      count: workCount,
      tone: 'blue',
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <section>
        <h2
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: 'var(--gray-50)',
            margin: '0 0 10px 0',
          }}
        >
          {t('countsHeading')}
        </h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: 8,
          }}
        >
          {counts.map((c) => (
            <CountCard key={c.key} label={c.label} count={c.count} tone={c.tone} />
          ))}
        </div>
      </section>

      <Section
        title={t('nextHeading')}
        count={next.length}
        emptyText={t('nextEmpty')}
      >
        {next.map((n, i) => (
          <div
            key={i}
            className="card"
            style={{ padding: 14, fontSize: 13, color: 'var(--gray-100)' }}
          >
            {n.summary}
          </div>
        ))}
      </Section>
    </div>
  )
}

function CountCard({
  label,
  count,
  tone,
}: {
  label: string
  count: number
  tone?: 'rose' | 'blue' | 'amber' | 'emerald'
}) {
  const accent =
    tone === 'rose'
      ? 'var(--rose-500)'
      : tone === 'blue'
      ? 'var(--blue-500)'
      : tone === 'amber'
      ? 'var(--amber-500)'
      : tone === 'emerald'
      ? 'var(--emerald-500)'
      : 'var(--text-secondary)'
  return (
    <div className="card" style={{ padding: '12px 14px' }}>
      <div
        className="mono"
        style={{ fontSize: 22, fontWeight: 600, color: accent, lineHeight: 1.1 }}
      >
        {count}
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>{label}</div>
    </div>
  )
}
