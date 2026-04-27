'use client'

import { useTranslations } from 'next-intl'
import { useRouter, useSearchParams } from 'next/navigation'

import ExecutorsSection from '../settings/ExecutorsSection'
import IntegrationsTab from '../settings/IntegrationsTab'
import LanguageSwitcher from '../settings/LanguageSwitcher'
import { I } from '../../lib/icons'

type SectionId = 'integrations' | 'executors' | 'language'

interface Section {
  id: SectionId
  icon: (p: { size?: number }) => React.ReactElement
}

const SECTIONS: Section[] = [
  { id: 'integrations', icon: I.Zap },
  { id: 'executors', icon: I.Brain },
  { id: 'language', icon: I.Settings },
]

function parseSection(raw: string | null): SectionId {
  if (raw === 'executors' || raw === 'language') return raw
  return 'integrations'
}

export default function SettingsPage() {
  const t = useTranslations('nexus.settings')
  const search = useSearchParams()
  const router = useRouter()
  const active = parseSection(search.get('section'))
  const activeSection = SECTIONS.find((s) => s.id === active) ?? SECTIONS[0]

  function go(id: SectionId) {
    const next = new URLSearchParams(search.toString())
    if (id === 'integrations') next.delete('section')
    else next.set('section', id)
    const qs = next.toString()
    router.replace(qs ? `/settings?${qs}` : '/settings')
  }

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '220px 1fr',
        height: '100%',
        minHeight: 0,
      }}
    >
      <nav
        style={{
          borderRight: '1px solid var(--border-subtle)',
          padding: '24px 16px',
        }}
      >
        <div
          style={{
            fontSize: 11,
            color: 'var(--text-tertiary)',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            padding: '0 8px 8px',
          }}
        >
          {t('title')}
        </div>
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`sb-item ${active === s.id ? 'active' : ''}`}
            onClick={() => go(s.id)}
          >
            <s.icon size={14} />
            <span className="label">{t(`sections.${s.id}.label`)}</span>
          </button>
        ))}
      </nav>

      <div style={{ overflow: 'auto', padding: 32 }}>
        <div style={{ maxWidth: 820 }}>
          <div style={{ marginBottom: 24 }}>
            <h1 className="page-title">{t(`sections.${activeSection.id}.label`)}</h1>
            <div className="page-sub">{t(`sections.${activeSection.id}.summary`)}</div>
          </div>
          {active === 'integrations' && <IntegrationsTab />}
          {active === 'executors' && <ExecutorsSection />}
          {active === 'language' && <LanguageSwitcher />}
        </div>
      </div>
    </div>
  )
}
