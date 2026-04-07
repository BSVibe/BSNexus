import { useMemo, useState, useCallback, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { agentsApi } from '../../api/agents'
import { useBoardStore } from '../../stores/boardStore'
import type { Agent } from '../../types/agent'
import type { Task } from '../../types/task'

const STATUS_COLORS: Record<string, string> = {
  online: '#22c55e',
  busy: '#3b82f6',
  offline: '#6b7280',
  budget_exceeded: '#ef4444',
}

const STATUS_LABELS: Record<string, string> = {
  online: 'idle',
  busy: 'working',
  offline: 'offline',
  budget_exceeded: 'budget exceeded',
}

const MIN_WIDTH = 200
const MAX_WIDTH = 600
const DEFAULT_WIDTH = 256

function getAgentCurrentTask(agent: Agent, allTasks: Task[]): Task | undefined {
  return allTasks.find((t) => t.status === 'in_progress' && t.agent_id === agent.id)
}

interface AgentSidebarProps {
  projectId: string
  onChatWithAgent?: (agent: Agent) => void
}

export default function AgentSidebar({ onChatWithAgent }: AgentSidebarProps) {
  const [width, setWidth] = useState(DEFAULT_WIDTH)
  const isDragging = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(DEFAULT_WIDTH)

  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsApi.list(),
  })

  const columns = useBoardStore((s) => s.columns)
  const allTasks = useMemo(() => Object.values(columns).flat(), [columns])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true
    startX.current = e.clientX
    startWidth.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [width])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const delta = startX.current - e.clientX
      setWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta)))
    }
    const handleMouseUp = () => {
      if (!isDragging.current) return
      isDragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  return (
    <aside
      className="h-full bg-stitch-surface-low border-l border-stitch-outline-variant/10 flex shrink-0 overflow-hidden"
      style={{ width }}
    >
      {/* Resize handle */}
      <div
        onMouseDown={handleMouseDown}
        className="w-1 hover:w-1 cursor-col-resize hover:bg-stitch-primary/30 active:bg-stitch-primary/50 transition-colors shrink-0"
      />

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Agents list */}
        <div className="flex-1 overflow-y-auto">
          <div className="px-4 py-3 border-b border-stitch-outline-variant/10">
            <h3 className="text-[10px] font-bold uppercase tracking-widest text-text-tertiary">Agents</h3>
          </div>

          {agents.length === 0 ? (
            <div className="px-4 py-8 text-center">
              <span className="material-symbols-outlined text-2xl text-text-tertiary mb-2 block">groups</span>
              <p className="text-xs text-text-tertiary">No agents configured</p>
            </div>
          ) : (
            <div className="py-1">
              {agents.map((agent) => {
                const currentTask = getAgentCurrentTask(agent, allTasks)
                const budgetPct = agent.monthly_budget_cents && agent.monthly_budget_cents > 0
                  ? Math.round((agent.current_month_spent_cents / agent.monthly_budget_cents) * 100)
                  : null

                return (
                  <button
                    key={agent.id}
                    onClick={() => onChatWithAgent?.(agent)}
                    className="w-full px-4 py-2.5 text-left hover:bg-stitch-surface-container transition-colors group"
                  >
                    <div className="flex items-center gap-2 mb-0.5">
                      <div
                        className="w-2 h-2 rounded-full shrink-0"
                        style={{ backgroundColor: STATUS_COLORS[currentTask ? 'busy' : agent.status] || '#6b7280' }}
                      />
                      <span className="text-xs font-bold text-text-primary truncate">{agent.name}</span>
                      <span className="text-[10px] text-text-tertiary ml-auto shrink-0">
                        {agent.title || agent.role}
                      </span>
                    </div>

                    {currentTask ? (
                      <div className="ml-4">
                        <p className="text-[10px] text-stitch-primary truncate">
                          {currentTask.status === 'in_progress' ? 'Working on' : currentTask.status}:
                        </p>
                        <p className="text-[10px] text-text-secondary truncate">{currentTask.title}</p>
                      </div>
                    ) : (
                      <p className="ml-4 text-[10px] text-text-tertiary">
                        {STATUS_LABELS[agent.status] || agent.status}
                      </p>
                    )}

                    {agent.monthly_budget_cents != null && (
                      <div className="ml-4 mt-1 flex items-center gap-2">
                        <div className="flex-1 h-0.5 bg-[#343439] rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${Math.min(budgetPct ?? 0, 100)}%`,
                              backgroundColor: (budgetPct ?? 0) >= 90 ? '#ffb4ab' : (budgetPct ?? 0) >= 70 ? '#eab308' : '#adc6ff',
                            }}
                          />
                        </div>
                        <span className="text-[9px] text-text-tertiary whitespace-nowrap">
                          ${((agent.current_month_spent_cents || 0) / 100).toFixed(0)}/${(agent.monthly_budget_cents / 100).toFixed(0)}
                        </span>
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Chat input area — pb-16 avoids overlap with fixed HelpButton */}
        <div className="border-t border-stitch-outline-variant/10 p-3 pb-16">
          <button
            onClick={() => agents[0] && onChatWithAgent?.(agents[0])}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg bg-stitch-surface border border-stitch-outline-variant/20 text-text-tertiary text-xs hover:border-stitch-primary/30 hover:text-text-secondary transition-colors"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>chat</span>
            Chat with Agent...
          </button>
        </div>
      </div>
    </aside>
  )
}
