import { useSearchParams } from 'react-router-dom'

import ExecutorsSection from '../components/settings/ExecutorsSection'
import IntegrationsTab from '../components/settings/IntegrationsTab'
import WorkerTokensSection from '../components/settings/WorkerTokensSection'
import { I } from '../lib/icons'

type SectionId = 'integrations' | 'executors' | 'worker-tokens'

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
      "Register LLM backends (LiteLLM direct, BSGateway proxy, claude-code, codex). One is marked default and used when a run doesn't pin a specific executor.",
  },
  {
    id: 'worker-tokens',
    label: 'Worker tokens',
    icon: I.GitBranch,
    summary:
      'Mint the install token remote workers need to register against this tenant. Manage registered workers (status, capabilities, heartbeat) here.',
  },
]

function parseSection(raw: string | null): SectionId {
  if (raw === 'executors' || raw === 'worker-tokens') return raw
  return 'integrations'
}

export default function SettingsPage() {
  const [search, setSearch] = useSearchParams()
  const active = parseSection(search.get('section'))
  const activeSection = SECTIONS.find((s) => s.id === active) ?? SECTIONS[0]

  function go(id: SectionId) {
    const next = new URLSearchParams(search)
    if (id === 'integrations') next.delete('section')
    else next.set('section', id)
    setSearch(next, { replace: true })
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
          {active === 'worker-tokens' && <WorkerTokensSection />}
        </div>
      </div>
    </div>
  )
}
