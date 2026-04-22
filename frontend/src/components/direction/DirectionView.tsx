import { useState } from 'react'

/**
 * Direction — the founder's single Chief-of-Staff conversation.
 * Wire: conversation_messages endpoint (to be reintroduced in P3+ API work).
 * Request chips appear inline when the extractor flags a message.
 */

interface DirectionViewProps {
  projectId?: string
}

export default function DirectionView({ projectId: _projectId }: DirectionViewProps) {
  const [draft, setDraft] = useState('')

  return (
    <div className="flex h-full flex-col bg-bg-primary">
      <header className="border-b border-border px-6 py-4">
        <h2 className="text-lg font-semibold text-text-primary">Direction</h2>
        <p className="text-sm text-text-secondary">
          Tell the company what you want. They&rsquo;ll decompose it, run it, and come back with results.
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto max-w-3xl space-y-4">
          <EmptyConversationState />
        </div>
      </div>

      <footer className="border-t border-border p-4">
        <div className="mx-auto max-w-3xl">
          <div className="flex gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="What do you want the company to work on?"
              className="flex-1 rounded-lg border border-border bg-bg-input px-4 py-3 text-sm text-text-primary placeholder:text-text-tertiary focus:border-accent focus:outline-none"
              rows={3}
            />
            <button
              type="button"
              disabled={!draft.trim()}
              className="self-end rounded-lg bg-accent px-4 py-2 text-sm font-medium text-bg-primary disabled:opacity-40"
            >
              Send
            </button>
          </div>
        </div>
      </footer>
    </div>
  )
}

function EmptyConversationState() {
  return (
    <div className="mt-16 text-center text-text-tertiary">
      <p className="text-sm">Say something to kick off. The company reads it, classifies it, and turns it into a request.</p>
    </div>
  )
}
