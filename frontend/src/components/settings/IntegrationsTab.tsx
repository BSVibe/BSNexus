import { useState } from 'react'

import IntegrationCard from './IntegrationCard'
import type { IntegrationConfig, IntegrationProvider } from '../../types/founder'

/**
 * Integrations tab — per-tenant configuration for the three optional
 * sibling services (BSage / BSGateway / BSupervisor). All three are
 * optional; when disabled, BSNexus falls back to Noop providers.
 *
 * Wire: calls /api/v1/integrations/config + /api/v1/integrations/{name}
 * (reintroduced in backend P3+).
 */

const CARDS: Array<{
  provider: IntegrationProvider
  label: string
  description: string
  accentClass: string
}> = [
  {
    provider: 'bsage',
    label: 'BSage — Knowledge',
    description:
      'Graph-backed project memory. When enabled, runs pull relevant notes into their composition.',
    accentClass: 'border-brand-emerald/30',
  },
  {
    provider: 'bsgateway',
    label: 'BSGateway — Model routing',
    description:
      'Cost-aware model selection via LiteLLM hook. Default LiteLLM is used when disabled.',
    accentClass: 'border-brand-amber/30',
  },
  {
    provider: 'bsupervisor',
    label: 'BSupervisor — Audit',
    description:
      'Sync pre-run rule evaluation (<50ms target). Fail-open by default; configurable.',
    accentClass: 'border-brand-rose/30',
  },
]

export default function IntegrationsTab() {
  const [configs, setConfigs] = useState<Record<IntegrationProvider, IntegrationConfig | null>>({
    bsage: null,
    bsgateway: null,
    bsupervisor: null,
  })

  async function handleSave(provider: IntegrationProvider, next: Partial<IntegrationConfig> & { apiKey?: string }) {
    // TODO (P6+): hook up to PATCH /api/v1/integrations/{provider}
    setConfigs((prev) => ({
      ...prev,
      [provider]: {
        provider,
        enabled: next.enabled ?? prev[provider]?.enabled ?? false,
        baseUrl: next.baseUrl ?? prev[provider]?.baseUrl ?? null,
        hasApiKey: Boolean(next.apiKey) || prev[provider]?.hasApiKey || false,
        extraConfig: { ...(prev[provider]?.extraConfig ?? {}), ...(next.extraConfig ?? {}) },
      },
    }))
  }

  async function handleTest(provider: IntegrationProvider): Promise<{ ok: boolean; detail?: string }> {
    // TODO (P6+): hook up to POST /api/v1/integrations/{provider}/test
    const cfg = configs[provider]
    if (!cfg?.baseUrl) return { ok: false, detail: 'Save a base URL first' }
    return { ok: true }
  }

  return (
    <div className="space-y-4">
      <header>
        <h2 className="text-lg font-semibold text-text-primary">Integrations</h2>
        <p className="text-sm text-text-tertiary">
          Connect the three optional BSVibe services. Each is tenant-scoped and encrypted at rest.
          BSNexus works fully without them.
        </p>
      </header>

      <div className="space-y-4">
        {CARDS.map((card) => (
          <IntegrationCard
            key={card.provider}
            provider={card.provider}
            label={card.label}
            description={card.description}
            accentClass={card.accentClass}
            config={configs[card.provider]}
            onSave={(next) => handleSave(card.provider, next)}
            onTest={() => handleTest(card.provider)}
          />
        ))}
      </div>
    </div>
  )
}
