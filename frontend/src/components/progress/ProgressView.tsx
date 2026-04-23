import { useQuery } from '@tanstack/react-query'

import { deliverablesApi } from '../../api/founder'
import { integrationsApi } from '../../api/integrations'
import type { Deliverable, IntegrationProvider } from '../../types/founder'

interface ProgressViewProps {
  projectId?: string
}

interface TrustCardData {
  provider: IntegrationProvider
  label: string
  accentClass: string
}

const TRUST_CARDS: TrustCardData[] = [
  {
    provider: 'bsage',
    label: 'BSage (knowledge)',
    accentClass: 'border-brand-emerald text-brand-emerald',
  },
  {
    provider: 'bsgateway',
    label: 'BSGateway (routing)',
    accentClass: 'border-brand-amber text-brand-amber',
  },
  {
    provider: 'bsupervisor',
    label: 'BSupervisor (audit)',
    accentClass: 'border-brand-rose text-brand-rose',
  },
]

export default function ProgressView({ projectId }: ProgressViewProps) {
  if (!projectId) {
    return (
      <div className="flex h-full items-center justify-center bg-bg-primary text-sm text-text-tertiary">
        Select a project first.
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-bg-primary">
      <header className="border-b border-border px-6 py-4">
        <h2 className="text-lg font-semibold text-text-primary">Progress</h2>
        <p className="text-sm text-text-secondary">
          What the company has shipped, plus whether its three sibling services are connected.
        </p>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <DeliverableTimeline projectId={projectId} />
        <TrustPanel />
      </div>
    </div>
  )
}

function DeliverableTimeline({ projectId }: { projectId: string }) {
  const { data: deliverables = [], isLoading } = useQuery<Deliverable[]>({
    queryKey: ['deliverables', projectId],
    queryFn: () => deliverablesApi.listForProject(projectId),
  })

  return (
    <section className="flex-1 overflow-y-auto px-6 py-4">
      <div className="mx-auto max-w-3xl">
        {isLoading ? (
          <p className="text-sm text-text-tertiary">Loading…</p>
        ) : deliverables.length === 0 ? (
          <p className="mt-8 text-center text-sm text-text-tertiary">
            No deliverables yet. Start a request in Direction.
          </p>
        ) : (
          <ul className="space-y-2">
            {deliverables.map((d) => (
              <li key={d.id}>
                <div className="rounded-lg border border-border bg-bg-card p-4">
                  <div className="flex items-center justify-between">
                    <span className="text-base font-semibold text-text-primary">{d.title}</span>
                    <span className="rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wider text-text-tertiary">
                      {d.status}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-text-tertiary">type: {d.type}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function TrustPanel() {
  const { data } = useQuery({
    queryKey: ['integrations'],
    queryFn: integrationsApi.list,
  })

  return (
    <aside className="w-80 border-l border-border bg-bg-surface p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-tertiary">
        Company services
      </h3>
      <div className="space-y-3">
        {TRUST_CARDS.map((card) => {
          const cfg = data?.[card.provider]
          const status: 'healthy' | 'degraded' | 'not_configured' = cfg?.enabled
            ? 'healthy'
            : 'not_configured'
          return (
            <div key={card.provider} className={`rounded-lg border bg-bg-card p-3 ${card.accentClass}`}>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm font-semibold">{card.label}</span>
                <span className="rounded-full border border-current px-2 py-0.5 text-[10px] uppercase tracking-wider">
                  {status === 'healthy' ? 'On' : 'Off'}
                </span>
              </div>
              <p className="text-xs text-text-tertiary">
                {cfg?.enabled
                  ? cfg.base_url ?? 'enabled but no base URL'
                  : 'Configure in Settings → Integrations.'}
              </p>
            </div>
          )
        })}
      </div>
    </aside>
  )
}
