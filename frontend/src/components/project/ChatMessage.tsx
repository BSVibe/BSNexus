import type { ChatMessageOut } from '../../api/agentChat'

const AGENT_COLORS = [
  '#3b82f6', '#8b5cf6', '#06b6d4', '#f59e0b', '#ec4899',
  '#10b981', '#6366f1', '#ef4444', '#14b8a6', '#f97316',
]

function getAgentColor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash)
  return AGENT_COLORS[Math.abs(hash) % AGENT_COLORS.length]
}

export default function ChatMessage({ message }: { message: ChatMessageOut }) {
  const isUser = message.role === 'user'
  const agentColor = message.agent_name ? getAgentColor(message.agent_name) : '#6b7280'
  const initial = message.agent_name?.[0]?.toUpperCase() || '?'

  return (
    <div className={`flex items-start gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
      {/* Avatar */}
      <div
        className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 text-[10px] font-bold ${
          isUser ? 'bg-stitch-surface-high text-text-secondary' : 'text-white'
        }`}
        style={isUser ? undefined : { backgroundColor: agentColor }}
      >
        {isUser ? (
          <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>person</span>
        ) : (
          initial
        )}
      </div>

      <div className={`max-w-[80%] ${isUser ? '' : ''}`}>
        {/* Agent name label */}
        {!isUser && message.agent_name && (
          <p className="text-[10px] font-bold mb-0.5" style={{ color: agentColor }}>
            {message.agent_name}
          </p>
        )}

        {/* Message bubble */}
        <div
          className={`rounded-lg px-3 py-2 text-sm ${
            isUser
              ? 'bg-stitch-primary/15 text-text-primary'
              : 'bg-stitch-surface-low border border-stitch-outline-variant/10 text-text-primary'
          }`}
        >
          <div className="whitespace-pre-wrap break-words">{message.content}</div>

          {/* Action notifications */}
          {message.actions.length > 0 && (
            <div className="mt-2 pt-2 border-t border-stitch-outline-variant/10 space-y-1">
              {message.actions.map((action, i) => (
                <div key={i} className="flex items-center gap-1.5 text-[10px] text-stitch-primary">
                  <span className="material-symbols-outlined" style={{ fontSize: '12px' }}>
                    {action.type === 'task_created' ? 'add_task' : action.type === 'goal_created' || action.type === 'goal_updated' ? 'flag' : 'edit'}
                  </span>
                  <span>
                    {action.type === 'task_created' && `Created task: ${action.title}`}
                    {action.type === 'goal_created' && `Set goal: ${action.title}`}
                    {action.type === 'goal_updated' && `Updated goal: ${action.title}`}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
