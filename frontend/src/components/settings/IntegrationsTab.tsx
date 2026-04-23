import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import IntegrationCard from './IntegrationCard'
import {
  integrationsApi,
  type IntegrationConfigList,
  type IntegrationConfigUpdate,
  type IntegrationTestResult,
} from '../../api/integrations'
import type { IntegrationProvider } from '../../types/founder'

const PROVIDERS: IntegrationProvider[] = ['bsage', 'bsgateway', 'bsupervisor']

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
    onSuccess: () => {
      // Force all subscribers (topbar pills, per-card state) to refetch.
      queryClient.invalidateQueries({ queryKey: ['integrations'] })
    },
  })

  if (isLoading) {
    return (
      <div style={{ padding: 16 }}>
        <p className="faded" style={{ fontSize: 13 }}>
          Loading…
        </p>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div style={{ padding: 16 }}>
        <div
          className="card"
          style={{
            padding: 16,
            fontSize: 13,
            color: 'var(--text-tertiary)',
            borderColor: 'rgba(244,63,94,0.3)',
          }}
        >
          Failed to load integration configs.
        </div>
      </div>
    )
  }

  async function handleSave(
    provider: IntegrationProvider,
    body: IntegrationConfigUpdate,
  ) {
    await updateMutation.mutateAsync({ provider, body })
  }

  async function handleTest(
    provider: IntegrationProvider,
  ): Promise<IntegrationTestResult> {
    return integrationsApi.test(provider)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {PROVIDERS.map((p) => (
        <IntegrationCard
          key={p}
          provider={p}
          config={data[p]}
          onSave={(body) => handleSave(p, body)}
          onTest={() => handleTest(p)}
        />
      ))}
    </div>
  )
}
