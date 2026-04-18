import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { designApi, type ScreenDetail, type ScreenSummary } from '../../api/design'
import ScreenRenderer, { type DesignTokens, type SpecNode } from './ScreenRenderer'

interface DesignViewProps {
  projectId: string
}

export default function DesignView({ projectId }: DesignViewProps) {
  const queryClient = useQueryClient()
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null)

  const screensQuery = useQuery({
    queryKey: ['design-screens', projectId],
    queryFn: () => designApi.listScreens(projectId),
    enabled: !!projectId,
  })

  const systemQuery = useQuery({
    queryKey: ['design-system', projectId],
    queryFn: () => designApi.getSystem(projectId),
    enabled: !!projectId,
  })

  const screenQuery = useQuery({
    queryKey: ['design-screen', projectId, selectedSlug],
    queryFn: () =>
      selectedSlug ? designApi.getScreen(projectId, selectedSlug) : Promise.resolve(null),
    enabled: !!selectedSlug,
  })

  const createScreen = useMutation({
    mutationFn: (name: string) => designApi.createScreen(projectId, { name, spec: {} }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['design-screens', projectId] })
      setSelectedSlug(created.slug)
    },
  })

  const deleteScreen = useMutation({
    mutationFn: (slug: string) => designApi.deleteScreen(projectId, slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['design-screens', projectId] })
      setSelectedSlug(null)
    },
  })

  const handleNewScreen = () => {
    const name = window.prompt('Screen name?')
    if (name && name.trim()) createScreen.mutate(name.trim())
  }

  const handleDelete = () => {
    if (!selectedSlug) return
    if (window.confirm(`Delete ${selectedSlug}.bsd?`)) deleteScreen.mutate(selectedSlug)
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left: screen list + design system summary */}
      <div className="w-72 shrink-0 border-r border-stitch-outline-variant/10 flex flex-col">
        <div className="px-4 py-3 border-b border-stitch-outline-variant/10 flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary">
            Design System
          </span>
        </div>
        <div className="px-4 py-3 border-b border-stitch-outline-variant/10">
          {systemQuery.isLoading ? (
            <p className="text-xs text-text-tertiary">Loading…</p>
          ) : systemQuery.data ? (
            <div className="text-xs text-text-secondary">
              <div className="font-bold text-text-primary mb-1">{systemQuery.data.name}</div>
              <div className="text-[10px] text-text-tertiary">
                {Object.keys(systemQuery.data.tokens).length} token groups ·{' '}
                {Object.keys(systemQuery.data.components).length} components
              </div>
              <div className="text-[10px] text-text-tertiary mt-1 truncate">
                {systemQuery.data.path}
              </div>
            </div>
          ) : (
            <p className="text-xs text-text-tertiary">No design system yet</p>
          )}
        </div>

        <div className="px-4 py-3 flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary">
            Screens (.bsd)
          </span>
          <button
            type="button"
            onClick={handleNewScreen}
            className="text-[10px] font-bold uppercase tracking-wider text-stitch-primary hover:underline"
          >
            + new
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 pb-3">
          {screensQuery.isLoading ? (
            <p className="px-2 text-xs text-text-tertiary">Loading…</p>
          ) : screensQuery.data && screensQuery.data.length > 0 ? (
            screensQuery.data.map((s: ScreenSummary) => (
              <button
                type="button"
                key={s.slug}
                onClick={() => setSelectedSlug(s.slug)}
                className={`w-full text-left rounded px-2 py-1.5 transition-colors ${
                  selectedSlug === s.slug
                    ? 'bg-stitch-primary/10 text-text-primary'
                    : 'text-text-secondary hover:bg-stitch-surface-low'
                }`}
              >
                <div className="text-xs truncate">{s.name}</div>
                {s.route && (
                  <div className="text-[10px] text-text-tertiary truncate">{s.route}</div>
                )}
              </button>
            ))
          ) : (
            <p className="px-2 text-xs text-text-tertiary italic">
              No screens yet. Ask the Designer agent to create one, or click + new.
            </p>
          )}
        </div>
      </div>

      {/* Right: selected screen detail */}
      <div className="flex-1 overflow-y-auto p-6">
        {!selectedSlug ? (
          <div className="h-full flex items-center justify-center text-text-tertiary text-sm">
            Select a screen to inspect its .bsd spec
          </div>
        ) : screenQuery.isLoading ? (
          <p className="text-text-tertiary text-sm">Loading…</p>
        ) : screenQuery.data ? (
          <ScreenDetailPanel
            screen={screenQuery.data}
            tokens={(systemQuery.data?.tokens ?? {}) as DesignTokens}
            onDelete={handleDelete}
          />
        ) : (
          <p className="text-rose-400 text-sm">Failed to load screen.</p>
        )}
      </div>
    </div>
  )
}

function ScreenDetailPanel({
  screen,
  tokens,
  onDelete,
}: {
  screen: ScreenDetail
  tokens: DesignTokens
  onDelete: () => void
}) {
  const [viewMode, setViewMode] = useState<'preview' | 'spec'>('preview')

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary mb-1">
            {screen.path}
          </div>
          <h2 className="text-xl font-bold text-text-primary">{screen.name}</h2>
          {screen.route && (
            <div className="text-xs text-text-tertiary mt-1">Route: {screen.route}</div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-md border border-stitch-outline-variant/20 overflow-hidden">
            <button
              type="button"
              onClick={() => setViewMode('preview')}
              className={`text-[10px] font-bold uppercase tracking-wider px-2 py-1 transition-colors ${
                viewMode === 'preview'
                  ? 'bg-stitch-primary/20 text-text-primary'
                  : 'text-text-tertiary hover:bg-stitch-surface-low'
              }`}
            >
              Preview
            </button>
            <button
              type="button"
              onClick={() => setViewMode('spec')}
              className={`text-[10px] font-bold uppercase tracking-wider px-2 py-1 transition-colors ${
                viewMode === 'spec'
                  ? 'bg-stitch-primary/20 text-text-primary'
                  : 'text-text-tertiary hover:bg-stitch-surface-low'
              }`}
            >
              JSON
            </button>
          </div>
          <button
            type="button"
            onClick={onDelete}
            className="text-[10px] font-bold uppercase tracking-wider text-rose-400 hover:underline"
          >
            delete
          </button>
        </div>
      </div>

      {screen.intent && (
        <Section title="Intent">
          <p className="text-sm text-text-secondary">{screen.intent}</p>
        </Section>
      )}

      {viewMode === 'preview' ? (
        <Section title="Preview">
          <ScreenRenderer spec={screen.spec as SpecNode} tokens={tokens} />
        </Section>
      ) : (
        <Section title="Spec">
          <pre className="text-[11px] text-text-secondary bg-stitch-surface-low rounded-md p-3 overflow-x-auto">
            {JSON.stringify(screen.spec, null, 2)}
          </pre>
        </Section>
      )}

      {screen.generated_code && (
        <Section title="Generated code">
          <pre className="text-[11px] text-text-secondary bg-stitch-surface-low rounded-md p-3 overflow-x-auto">
            {screen.generated_code}
          </pre>
        </Section>
      )}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h3 className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary mb-2">
        {title}
      </h3>
      {children}
    </div>
  )
}
