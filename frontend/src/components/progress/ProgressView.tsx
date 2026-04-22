/**
 * Progress — deliverable timeline + three trust cards.
 *
 * Left: vertical timeline of Deliverables (title, type, version count,
 * status). Right: three trust cards for BSage / BSGateway / BSupervisor
 * showing whether each is connected + degraded/healthy status.
 */

interface ProgressViewProps {
  projectId?: string
}

type TrustStatus = 'healthy' | 'degraded' | 'not_configured'

interface TrustCardData {
  provider: 'bsage' | 'bsgateway' | 'bsupervisor'
  label: string
  status: TrustStatus
  accentClass: string
  detail: string
}

const TRUST_CARDS: TrustCardData[] = [
  {
    provider: 'bsage',
    label: 'BSage (knowledge)',
    status: 'not_configured',
    accentClass: 'border-brand-emerald text-brand-emerald',
    detail: 'Configure in Settings → Integrations to enrich runs with project knowledge.',
  },
  {
    provider: 'bsgateway',
    label: 'BSGateway (routing)',
    status: 'not_configured',
    accentClass: 'border-brand-amber text-brand-amber',
    detail: 'Cost-aware model routing. Uses default LiteLLM when unconfigured.',
  },
  {
    provider: 'bsupervisor',
    label: 'BSupervisor (audit)',
    status: 'not_configured',
    accentClass: 'border-brand-rose text-brand-rose',
    detail: 'Pre-run safety checks. Runs proceed without audit when unconfigured.',
  },
]

export default function ProgressView({ projectId: _projectId }: ProgressViewProps) {
  return (
    <div className="flex h-full flex-col overflow-hidden bg-bg-primary">
      <header className="border-b border-border px-6 py-4">
        <h2 className="text-lg font-semibold text-text-primary">Progress</h2>
        <p className="text-sm text-text-secondary">
          What the company has shipped, plus whether its three sibling services are healthy.
        </p>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <DeliverableTimeline />
        <TrustPanel />
      </div>
    </div>
  )
}

function DeliverableTimeline() {
  return (
    <section className="flex-1 overflow-y-auto px-6 py-4">
      <div className="mx-auto max-w-3xl">
        <div className="mt-8 text-center text-text-tertiary">
          <p className="text-sm">No deliverables yet. Start a request in Direction.</p>
        </div>
      </div>
    </section>
  )
}

function TrustPanel() {
  return (
    <aside className="w-80 border-l border-border bg-bg-surface p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-tertiary">
        Company services
      </h3>
      <div className="space-y-3">
        {TRUST_CARDS.map((card) => (
          <TrustCard key={card.provider} card={card} />
        ))}
      </div>
    </aside>
  )
}

function TrustCard({ card }: { card: TrustCardData }) {
  return (
    <div className={`rounded-lg border bg-bg-card p-3 ${card.accentClass}`}>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-sm font-semibold">{card.label}</span>
        <StatusBadge status={card.status} />
      </div>
      <p className="text-xs text-text-tertiary">{card.detail}</p>
    </div>
  )
}

function StatusBadge({ status }: { status: TrustStatus }) {
  const label = status === 'healthy' ? 'Healthy' : status === 'degraded' ? 'Degraded' : 'Off'
  return (
    <span className="rounded-full border border-current px-2 py-0.5 text-[10px] uppercase tracking-wider">
      {label}
    </span>
  )
}
