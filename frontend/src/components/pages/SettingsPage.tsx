'use client'

import { useTranslations } from 'next-intl'

import IntegrationsTab from '../settings/IntegrationsTab'

/**
 * SettingsPage — Integrations admin only.
 *
 * G7.5a (post-PR #93 recovery): the Integrations section was wrongly
 * classified DELETE in PR #93 (file-disposition.md actually marks
 * `IntegrationsTab` / `IntegrationCard` / `api/integrations.ts` as
 * REVIEW_LATER, not DELETE) and replaced with a "redesign 예정"
 * placeholder in G7.1. Restored on top of the existing
 * ``api/v1/integrations`` backend (which was always live but
 * unmounted).
 *
 * Language switcher lives in the sidebar (BSVibe common UI spec) so
 * the duplicate Settings section is gone — no nav, no second copy of
 * the same control.
 */
export default function SettingsPage() {
  const t = useTranslations('nexus.settings')
  return (
    <div className="settings-main">
      <div className="settings-main__inner">
        <div style={{ marginBottom: 24 }}>
          <h1 className="page-title">{t('sections.integrations.label')}</h1>
          <div className="page-sub">{t('sections.integrations.summary')}</div>
        </div>
        <IntegrationsTab />
      </div>
    </div>
  )
}
