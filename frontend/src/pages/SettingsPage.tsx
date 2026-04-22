import { useState } from 'react'

import IntegrationsTab from '../components/settings/IntegrationsTab'
import Header from '../components/layout/Header'

type Tab = 'integrations' | 'executors'

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'integrations', label: 'Integrations' },
  { key: 'executors', label: 'Executors' },
]

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>('integrations')

  return (
    <>
      <Header title="Settings" />
      <div className="p-6">
        <nav className="mb-6 flex gap-1 border-b border-border">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-sm transition-colors ${
                tab === t.key
                  ? 'border-b-2 border-accent text-text-primary'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <div className="mx-auto max-w-3xl">
          {tab === 'integrations' && <IntegrationsTab />}
          {tab === 'executors' && (
            <div className="rounded-lg border border-border bg-bg-card p-6 text-sm text-text-tertiary">
              <p>Executor configuration returns in a follow-up PR.</p>
              <p className="mt-2">
                v1 focuses on the three sibling integrations; BSGateway covers
                model selection when enabled.
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
