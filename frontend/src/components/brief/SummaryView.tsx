'use client'

import { useTranslations } from 'next-intl'

import { Section } from './sections'
import type { BriefResponse } from '../../types/founder'

/**
 * Summary tab — one-screen overview of the four section counts plus
 * the "next" list (the 5th section in the old Brief). Sits in the new
 * 요약 tab so the next-up queue keeps a home now that the monolithic
 * Brief is split.
 */
export default function SummaryView({ brief }: { brief: BriefResponse | null | undefined }) {
  const t = useTranslations('nexus.project.summary')
  const tBrief = useTranslations('nexus.brief')

  if (!brief) {
    return (
      <div style={{ padding: 24, color: 'var(--text-tertiary)', fontSize: 13 }}>
        {tBrief('loading')}
      </div>
    )
  }

  const { shipped, needs_decision, blocked, running, next } = brief.sections

  const counts: { key: string; label: string; count: number; tone?: 'rose' | 'blue' | 'amber' | 'emerald' }[] = [
    { key: 'shipped', label: t('shippedLabel'), count: shipped.length, tone: 'emerald' },
    { key: 'needsDecision', label: t('needsDecisionLabel'), count: needs_decision.length, tone: needs_decision.length > 0 ? 'rose' : undefined },
    { key: 'running', label: t('runningLabel'), count: running.length, tone: 'blue' },
    { key: 'blocked', label: t('blockedLabel'), count: blocked.length, tone: blocked.length > 0 ? 'rose' : undefined },
  ]

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 24 }}>
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

      <Section title={t('nextHeading')} count={next.length} emptyText={t('nextEmpty')}>
        {next.map((n, i) => (
          <div key={i} className="card" style={{ padding: 14, fontSize: 13, color: 'var(--gray-100)' }}>
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
