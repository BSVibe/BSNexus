import Header from '../components/layout/Header'

/**
 * Dashboard — project list placeholder.
 *
 * v1 stub: projects API will be reintroduced in the follow-up PR
 * together with the resource routers (projects, requests, deliverables,
 * decisions, integrations). For now this keeps the nav intact.
 */
export default function DashboardPage() {
  return (
    <>
      <Header title="Dashboard" />
      <div className="p-6">
        <div className="mx-auto max-w-3xl">
          <div className="rounded-lg border border-border bg-bg-card p-6 text-sm text-text-tertiary">
            <h2 className="mb-2 text-base font-semibold text-text-primary">
              Founder metaphor — backend reshape in progress
            </h2>
            <p>
              The backend has been rebuilt around Requests, ExecutionRuns, Deliverables, and Decisions
              (7 tables retired, 5 added). The project list and per-project surfaces
              (Direction / Progress / Decisions / Inside) ship in the next PR once the resource
              routers are reintroduced atop the new schema.
            </p>
            <p className="mt-2">
              Meanwhile, check the <a href="/settings" className="text-accent underline">Settings → Integrations</a>{' '}
              tab to see the per-tenant config surface for BSage / BSGateway / BSupervisor.
            </p>
          </div>
        </div>
      </div>
    </>
  )
}
