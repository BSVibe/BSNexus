import type { Agent } from '../../types/agent'

const STATUS_COLORS: Record<string, string> = {
  online: '#22c55e',
  busy: '#3b82f6',
  offline: '#6b7280',
  budget_exceeded: '#ef4444',
}

interface Props {
  agents: Agent[]
  query: string
  selectedIndex: number
  onSelect: (agent: Agent) => void
  onDismiss: () => void
}

export default function MentionAutocomplete({ agents, query, selectedIndex, onSelect, onDismiss }: Props) {
  const filtered = agents.filter((a) =>
    a.name.toLowerCase().startsWith(query.toLowerCase()),
  )

  if (filtered.length === 0) {
    onDismiss()
    return null
  }

  return (
    <div className="absolute bottom-full left-0 right-0 mb-1 bg-stitch-surface-container border border-stitch-outline-variant/20 rounded-lg shadow-xl overflow-hidden z-10 max-h-48 overflow-y-auto">
      {filtered.map((agent, i) => (
        <button
          key={agent.id}
          onMouseDown={(e) => {
            e.preventDefault()
            onSelect(agent)
          }}
          className={`w-full px-3 py-2 flex items-center gap-2 text-left text-sm transition-colors ${
            i === selectedIndex ? 'bg-stitch-primary/15 text-text-primary' : 'text-text-secondary hover:bg-stitch-surface-high'
          }`}
        >
          <div
            className="w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: STATUS_COLORS[agent.status] || '#6b7280' }}
          />
          <span className="font-medium truncate">{agent.name}</span>
          <span className="text-[10px] text-text-tertiary ml-auto shrink-0">{agent.role}</span>
        </button>
      ))}
    </div>
  )
}
