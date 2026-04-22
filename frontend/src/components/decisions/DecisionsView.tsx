/**
 * Decisions — inbox of items the founder must approve.
 *
 * Blocking decisions (associated request stalls until resolved) rise to
 * the top. Non-blocking are informational (company decided X on your
 * behalf; confirm or override).
 */

interface DecisionsViewProps {
  projectId?: string
}

export default function DecisionsView({ projectId: _projectId }: DecisionsViewProps) {
  return (
    <div className="flex h-full flex-col bg-bg-primary">
      <header className="border-b border-border px-6 py-4">
        <h2 className="text-lg font-semibold text-text-primary">Decisions</h2>
        <p className="text-sm text-text-secondary">
          The company waits here when it needs you. Blocking items first.
        </p>
      </header>

      <section className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto max-w-3xl">
          <div className="mt-8 text-center text-text-tertiary">
            <p className="text-sm">Inbox clear.</p>
          </div>
        </div>
      </section>
    </div>
  )
}
