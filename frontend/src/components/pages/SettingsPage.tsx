'use client'

import { useTranslations } from 'next-intl'
import { useRouter, useSearchParams } from 'next/navigation'

import IntegrationsTab from '../settings/IntegrationsTab'
import LanguageSwitcher from '../settings/LanguageSwitcher'
import { I } from '../../lib/icons'

type SectionId = 'integrations' | 'language'

interface Section {
  id: SectionId
  icon: (p: { size?: number }) => React.ReactElement
}

const SECTIONS: Section[] = [
  { id: 'integrations', icon: I.Zap },
  { id: 'language', icon: I.Settings },
]

function parseSection(raw: string | null): SectionId {
  if (raw === 'language') return raw
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
    <div className="settings-grid">
      <nav className="settings-nav">
        <div className="settings-nav__title">{t('title')}</div>
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

      <div className="settings-main">
        <div className="settings-main__inner">
          <div style={{ marginBottom: 24 }}>
            <h1 className="page-title">{t(`sections.${activeSection.id}.label`)}</h1>
            <div className="page-sub">{t(`sections.${activeSection.id}.summary`)}</div>
          </div>
          {active === 'integrations' && <IntegrationsTab />}
          {active === 'language' && <LanguageSwitcher />}
        </div>
      </div>
    </div>
  )
}
