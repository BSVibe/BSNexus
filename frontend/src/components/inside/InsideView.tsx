/**
 * Inside — opt-in transparency panel.
 *
 * Tree of execution_runs for the selected request + the composition
 * snapshot that drove each run. Shows whether BSage/BSGateway/
 * BSupervisor participated (composition.source = bsage|local,
 * audit.degraded flag, etc.).
 *
 * For debugging + trust. Not default-visible.
 */

interface InsideViewProps {
  projectId?: string
  requestId?: string
}

export default function InsideView({ projectId: _projectId, requestId: _requestId }: InsideViewProps) {
  return (
    <div className="flex h-full flex-col bg-bg-primary">
      <header className="border-b border-border px-6 py-4">
        <h2 className="text-lg font-semibold text-text-primary">Inside</h2>
        <p className="text-sm text-text-secondary">
          How the company processed a request. Runs, composition, and which services touched it.
        </p>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <RunTreePanel />
        <RunDetailPanel />
      </div>
    </div>
  )
}

function RunTreePanel() {
  return (
    <section className="w-80 border-r border-border bg-bg-surface p-4">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-tertiary">
        Runs
      </h3>
      <div className="mt-8 text-center text-text-tertiary">
        <p className="text-sm">Select a request from Progress to inspect.</p>
      </div>
    </section>
  )
}

function RunDetailPanel() {
  return (
    <section className="flex-1 overflow-y-auto px-6 py-4">
      <div className="mt-8 text-center text-text-tertiary">
        <p className="text-sm">Composition snapshot + tool log will render here.</p>
      </div>
    </section>
  )
}
