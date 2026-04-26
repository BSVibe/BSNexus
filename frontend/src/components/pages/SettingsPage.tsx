'use client'

import { useRouter, useSearchParams } from 'next/navigation'

import ExecutorsSection from '../settings/ExecutorsSection'
import IntegrationsTab from '../settings/IntegrationsTab'
import { I } from '../../lib/icons'

type SectionId = 'integrations' | 'executors'

interface Section {
  id: SectionId
  label: string
  icon: (p: { size?: number }) => React.ReactElement
  summary: string
}

const SECTIONS: Section[] = [
  {
    id: 'integrations',
    label: 'Integrations',
    icon: I.Zap,
    summary:
      'Connect BSNexus to sibling services. Keys are stored tenant-scoped and encrypted at rest; only whether a key is present is ever returned.',
  },
  {
    id: 'executors',
    label: 'Executors',
    icon: I.Brain,
    summary:
      'Register LLM backends and remote workers. LLM executors (LiteLLM / BSGateway / claude-code / codex) handle composition runs; remote workers register with an install token and execute coding tasks with their local CLI.',
  },
]

function parseSection(raw: string | null): SectionId {
  if (raw === 'executors') return raw
  return 'integrations'
}

export default function SettingsPage() {
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
          Settings
        </div>
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`sb-item ${active === s.id ? 'active' : ''}`}
            onClick={() => go(s.id)}
          >
            <s.icon size={14} />
            <span className="label">{s.label}</span>
          </button>
        ))}
      </nav>

      <div style={{ overflow: 'auto', padding: 32 }}>
        <div style={{ maxWidth: 820 }}>
          <div style={{ marginBottom: 24 }}>
            <h1 className="page-title">{activeSection.label}</h1>
            <div className="page-sub">{activeSection.summary}</div>
          </div>
          {active === 'integrations' && <IntegrationsTab />}
          {active === 'executors' && <ExecutorsSection />}
        </div>
      </div>
    </div>
  )
}
