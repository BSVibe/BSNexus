'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import IntegrationsTab from '../settings/IntegrationsTab'
import LLMDispatchSection from '../settings/LLMDispatchSection'

type Section = 'llmDispatch' | 'integrations'

/**
 * SettingsPage — two sections: LLM Dispatch + Integrations.
 *
 * G7.5b adds LLM Dispatch (per-tenant executor config). Without it the
 * founder cannot supply a worker-pool URL or a provider key, so no
 * run can ever execute — quality engineering is gated on this.
 *
 * Integrations remains the BSage / BSupervisor sibling-service hooks.
 * Language switcher lives in the sidebar (BSVibe common UI spec) and
 * is not duplicated here.
 */
export default function SettingsPage() {
  const t = useTranslations('nexus.settings')
  const [section, setSection] = useState<Section>('llmDispatch')

  return (
    <div className="settings-main">
      <div className="settings-main__inner">
        <div style={{ marginBottom: 20 }}>
          <h1 className="page-title">{t('title')}</h1>
        </div>
        <nav
          style={{
            display: 'flex',
            gap: 4,
            marginBottom: 16,
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <SectionTab
            active={section === 'llmDispatch'}
            onClick={() => setSection('llmDispatch')}
            label={t('sections.llmDispatch.label')}
          />
          <SectionTab
            active={section === 'integrations'}
            onClick={() => setSection('integrations')}
            label={t('sections.integrations.label')}
          />
        </nav>

        {section === 'llmDispatch' && (
          <>
            <p
              className="faded"
              style={{ fontSize: 13, marginBottom: 12, marginTop: 0 }}
            >
              {t('sections.llmDispatch.summary')}
            </p>
            <LLMDispatchSection />
          </>
        )}
        {section === 'integrations' && (
          <>
            <p
              className="faded"
              style={{ fontSize: 13, marginBottom: 12, marginTop: 0 }}
            >
              {t('sections.integrations.summary')}
            </p>
            <IntegrationsTab />
          </>
        )}
      </div>
    </div>
  )
}

function SectionTab({
  active,
  onClick,
  label,
}: {
  active: boolean
  onClick: () => void
  label: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="btn btn-sm"
      style={{
        background: 'transparent',
        border: 'none',
        borderBottom: `2px solid ${active ? 'var(--blue-500)' : 'transparent'}`,
        borderRadius: 0,
        color: active ? 'var(--gray-50)' : 'var(--text-secondary)',
        padding: '10px 14px',
        fontSize: 13,
        fontWeight: active ? 600 : 500,
        minHeight: 44,
      }}
      aria-pressed={active}
    >
      {label}
    </button>
  )
}
