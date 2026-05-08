'use client'

import { useTranslations } from 'next-intl'
import { Badge } from './Badge'
import type { Tone } from '../../lib/tone'
import type { ProofState } from '../../types/founder'

const TONE_BY_STATE: Record<ProofState, Tone> = {
  verified: 'emerald',
  verifying: 'amber',
  verification_failed: 'rose',
  verification_missing: 'gray',
  human_review_required: 'indigo',
  not_applicable: 'gray',
}

/**
 * ProofBadge — 5-state proof badge for Deliverable cards (decision-locks A1).
 *
 * Tone palette mirrors the design tokens:
 * - verified → emerald (BSage-style success)
 * - verifying → amber (in-flight, like BSGateway pending)
 * - verification_failed → rose (BSupervisor-style fail)
 * - verification_missing / not_applicable → gray (no proof / opt-out)
 * - human_review_required → indigo (founder action needed)
 */
export function ProofBadge({ state, dot = true }: { state: ProofState; dot?: boolean }) {
  const t = useTranslations('nexus.brief.proof')
  const tone = TONE_BY_STATE[state] ?? 'gray'
  return (
    <Badge tone={tone} dot={dot}>
      {t(state)}
    </Badge>
  )
}
