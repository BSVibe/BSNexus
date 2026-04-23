import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import IntegrationCard from './IntegrationCard'
import {
  integrationsApi,
  type IntegrationConfigList,
  type IntegrationConfigResponse,
  type IntegrationConfigUpdate,
  type IntegrationTestResult,
} from '../../api/integrations'
import type { IntegrationProvider } from '../../types/founder'

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
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useQuery<IntegrationConfigList>({
    queryKey: ['integrations'],
    queryFn: integrationsApi.list,
  })

  const updateMutation = useMutation({
    mutationFn: ({
      provider,
      body,
    }: {
      provider: IntegrationProvider
      body: IntegrationConfigUpdate
    }) => integrationsApi.update(provider, body),
    onSuccess: (updated, { provider }) => {
      queryClient.setQueryData<IntegrationConfigList | undefined>(
        ['integrations'],
        (prev) => (prev ? { ...prev, [provider]: updated } : prev),
      )
    },
  })

  if (isLoading) {
    return (
      <div className="space-y-4">
        <header>
          <h2 className="text-lg font-semibold text-text-primary">Integrations</h2>
          <p className="text-sm text-text-tertiary">Loading…</p>
        </header>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="space-y-4">
        <header>
          <h2 className="text-lg font-semibold text-text-primary">Integrations</h2>
        </header>
        <div className="rounded-lg border border-error bg-bg-card p-4 text-sm text-error">
          Failed to load integration configs.
        </div>
      </div>
    )
  }

  async function handleSave(provider: IntegrationProvider, body: IntegrationConfigUpdate) {
    await updateMutation.mutateAsync({ provider, body })
  }

  async function handleTest(provider: IntegrationProvider): Promise<IntegrationTestResult> {
    return integrationsApi.test(provider)
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
            config={data[card.provider] as IntegrationConfigResponse | null}
            onSave={(body) => handleSave(card.provider, body)}
            onTest={() => handleTest(card.provider)}
          />
        ))}
      </div>
    </div>
  )
}
