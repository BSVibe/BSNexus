import { useSearchParams } from 'react-router-dom'

import IntegrationsTab from '../components/settings/IntegrationsTab'
import { I } from '../lib/icons'

type SectionId = 'integrations' | 'executors' | 'worker-tokens'

interface Section {
  id: SectionId
  label: string
  icon: (p: { size?: number }) => React.ReactElement
  available: boolean
  summary: string
}

const SECTIONS: Section[] = [
  {
    id: 'integrations',
    label: 'Integrations',
    icon: I.Zap,
    available: true,
    summary:
      'Connect BSNexus to sibling services. Keys are stored tenant-scoped and encrypted at rest; only whether a key is present is ever returned.',
  },
  {
    id: 'executors',
    label: 'Executors',
    icon: I.Brain,
    available: false,
    summary:
      'Choose which LLM / local-agent executor runs each kind of request. Ships in v0.2 once BSGateway routing and local claude-code workers are wired end-to-end.',
  },
  {
    id: 'worker-tokens',
    label: 'Worker tokens',
    icon: I.GitBranch,
    available: false,
    summary:
      'Issue install tokens for remote worker pools (self-hosted claude-code / codex runners). Ships in v0.2 alongside WorkerWatchdog re-enablement.',
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
            {!s.available && (
              <span className="mono faded" style={{ fontSize: 10 }}>
                v0.2
              </span>
            )}
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
          {active !== 'integrations' && <ComingSoon label={activeSection.label} />}
        </div>
      </div>
    </div>
  )
}

function ComingSoon({ label }: { label: string }) {
  return (
    <div
      className="card"
      style={{
        padding: 32,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-start',
        gap: 12,
        border: '1px dashed var(--border-default)',
      }}
    >
      <div
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
          padding: '4px 10px',
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--r-full)',
          fontSize: 11,
          color: 'var(--text-tertiary)',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
        }}
      >
        <I.Sparkle size={12} /> v0.2
      </div>
      <div style={{ fontSize: 15, fontWeight: 500, color: 'var(--gray-100)' }}>
        {label} ships in v0.2.
      </div>
      <p
        style={{
          margin: 0,
          fontSize: 13,
          color: 'var(--text-secondary)',
          lineHeight: '20px',
        }}
      >
        This section is intentionally not interactive yet — see{' '}
        <a href="/dashboard" style={{ color: 'var(--accent)' }}>
          Dashboard
        </a>{' '}
        for what's live today.
      </p>
    </div>
  )
}
