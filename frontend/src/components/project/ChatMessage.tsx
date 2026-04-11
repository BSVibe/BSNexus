import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
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

/** Highlight @mentions in plain text (user messages) */
function renderWithMentions(text: string) {
  const parts = text.split(/(@\S+)/g)
  return parts.map((part, i) =>
    part.startsWith('@') ? (
      <span key={i} className="font-semibold text-stitch-primary">{part}</span>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}

/**
 * Pre-process markdown to wrap @mentions in a marker that the custom
 * ReactMarkdown ``strong`` renderer can pick up and style blue.
 * The pattern ``**@Name**`` is already markdown-safe bold, and the
 * custom component override below checks whether the bold text starts
 * with ``@`` and renders it as a ``text-stitch-primary`` span instead
 * of a plain ``<strong>``.
 */
function highlightMentionsInMarkdown(content: string): string {
  return content.replace(/@([A-Za-z가-힣][\w\s가-힣]*?)(?=[\s,.)：:;!?]|$)/g, '**@$1**')
}

function formatTime(iso: string | undefined): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

export default function ChatMessage({ message, typing }: { message: ChatMessageOut; typing?: boolean }) {
  const isUser = message.role === 'user'
  const agentColor = message.agent_name ? getAgentColor(message.agent_name) : '#6b7280'
  const initial = message.agent_name?.[0]?.toUpperCase() || '?'
  const time = formatTime(message.created_at)

  const processedContent = useMemo(
    () => (isUser ? message.content : highlightMentionsInMarkdown(message.content)),
    [message.content, isUser],
  )

  if (isUser) {
    return (
      <div className="flex flex-col items-end gap-0.5">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-stitch-primary/20 px-3 py-2 text-sm text-text-primary">
          <div className="whitespace-pre-wrap break-words">{renderWithMentions(message.content)}</div>
        </div>
        {time && <span className="text-[10px] text-text-tertiary mr-1">{time}</span>}
      </div>
    )
  }

  return (
    <div className="flex items-start gap-2">
      {/* Agent avatar */}
      <div
        className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 text-[10px] font-bold text-white mt-0.5"
        style={{ backgroundColor: agentColor }}
      >
        {initial}
      </div>

      <div className="max-w-[85%] min-w-0">
        {/* Agent name + timestamp */}
        {message.agent_name && (
          <div className="flex items-center gap-2 mb-1 ml-1">
            <p className="text-[10px] font-bold" style={{ color: agentColor }}>
              {message.agent_name}
            </p>
            {time && <span className="text-[10px] text-text-tertiary">{time}</span>}
          </div>
        )}

        {/* Message bubble */}
        <div className="rounded-2xl rounded-tl-sm bg-stitch-surface border border-stitch-outline-variant/10 px-3 py-2 text-sm text-text-primary">
          {typing ? (
            <div className="flex items-center gap-1 py-0.5">
              <div className="w-1.5 h-1.5 bg-text-tertiary rounded-full animate-bounce" />
              <div className="w-1.5 h-1.5 bg-text-tertiary rounded-full animate-bounce" style={{ animationDelay: '0.15s' }} />
              <div className="w-1.5 h-1.5 bg-text-tertiary rounded-full animate-bounce" style={{ animationDelay: '0.3s' }} />
            </div>
          ) : (
            <div className="prose prose-sm max-w-none prose-invert overflow-hidden [&_p]:my-1 [&_p]:leading-relaxed [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-base [&_h1]:font-bold [&_h1]:mt-3 [&_h1]:mb-1 [&_h2]:text-sm [&_h2]:font-bold [&_h2]:mt-3 [&_h2]:mb-1 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1 [&_h4]:text-sm [&_h4]:font-semibold [&_h4]:mt-2 [&_h4]:mb-1 [&_strong]:text-text-primary [&_hr]:my-2 [&_blockquote]:border-stitch-primary/40 [&_blockquote]:text-text-secondary [&_a]:text-stitch-primary [&_pre]:overflow-x-auto [&_pre]:max-w-full">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  table({ children, ...props }) {
                    return (
                      <div className="overflow-x-auto max-w-full my-2 -mx-1">
                        <table className="text-xs min-w-full border-collapse" {...props}>
                          {children}
                        </table>
                      </div>
                    )
                  },
                  th({ children, ...props }) {
                    return (
                      <th className="px-2 py-1 border border-stitch-outline-variant/20 bg-stitch-surface-container text-left whitespace-nowrap" {...props}>
                        {children}
                      </th>
                    )
                  },
                  td({ children, ...props }) {
                    return (
                      <td className="px-2 py-1 border border-stitch-outline-variant/20 align-top" {...props}>
                        {children}
                      </td>
                    )
                  },
                  // @mentions are pre-processed into **@Name** (markdown
                  // bold). This override checks whether the bold text
                  // starts with @ and renders it as a blue span so
                  // mentions look the same in user and assistant bubbles.
                  strong({ children }) {
                    const text = typeof children === 'string'
                      ? children
                      : Array.isArray(children) ? children.map(String).join('') : String(children ?? '')
                    if (text.startsWith('@')) {
                      return <span className="font-semibold text-stitch-primary">{text}</span>
                    }
                    return <strong>{children}</strong>
                  },
                  code({ className, children, ...props }) {
                    const match = /language-(\w+)/.exec(className || '')
                    const codeStr = String(children).replace(/\n$/, '')
                    if (match) {
                      return (
                        <div className="bg-[#1f1f1f] rounded-lg overflow-hidden my-2 border border-stitch-outline-variant/10">
                          <div className="flex items-center justify-between px-3 py-1.5 border-b border-stitch-outline-variant/20">
                            <span className="text-text-tertiary text-[10px] uppercase tracking-widest">{match[1]}</span>
                          </div>
                          <SyntaxHighlighter
                            style={oneDark}
                            language={match[1]}
                            PreTag="div"
                            customStyle={{ margin: 0, borderRadius: 0, background: '#1f1f1f', fontSize: '12px' }}
                          >
                            {codeStr}
                          </SyntaxHighlighter>
                        </div>
                      )
                    }
                    return (
                      <code
                        className={`${className || ''} bg-stitch-surface-container text-stitch-primary px-1 py-0.5 rounded text-xs font-mono`}
                        {...props}
                      >
                        {children}
                      </code>
                    )
                  },
                }}
              >
                {processedContent}
              </ReactMarkdown>
            </div>
          )}

          {/* Action notifications */}
          {!typing && message.actions.length > 0 && (
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
