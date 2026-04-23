import IntegrationsTab from '../components/settings/IntegrationsTab'
import { I } from '../lib/icons'

export default function SettingsPage() {
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
        <button type="button" className="sb-item active">
          <I.Zap size={14} />
          <span className="label">Integrations</span>
        </button>
        <button type="button" className="sb-item" style={{ opacity: 0.5 }} disabled>
          <I.Brain size={14} />
          <span className="label">Executors</span>
          <span className="mono faded" style={{ fontSize: 10 }}>
            v0.2
          </span>
        </button>
        <button type="button" className="sb-item" style={{ opacity: 0.5 }} disabled>
          <I.GitBranch size={14} />
          <span className="label">Worker tokens</span>
          <span className="mono faded" style={{ fontSize: 10 }}>
            v0.2
          </span>
        </button>
        <button type="button" className="sb-item" style={{ opacity: 0.5 }} disabled>
          <I.Data size={14} />
          <span className="label">Billing</span>
          <span className="mono faded" style={{ fontSize: 10 }}>
            v0.3
          </span>
        </button>
      </nav>
      <div style={{ overflow: 'auto', padding: 32 }}>
        <div style={{ maxWidth: 820 }}>
          <div style={{ marginBottom: 24 }}>
            <h1 className="page-title">Integrations</h1>
            <div className="page-sub">
              Connect BSNexus to sibling services. Keys are stored tenant-scoped and
              encrypted at rest; only whether a key is present is ever returned.
            </div>
          </div>
          <IntegrationsTab />
        </div>
      </div>
    </div>
  )
}
