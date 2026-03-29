import ReactMarkdown from 'react-markdown'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { ChatMessage as ChatMessageType } from '../../stores/architectStore'
import { Bot, User } from 'lucide-react'

interface Props {
  message: ChatMessageType
}

function formatTime(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

function sanitizeContent(content: string): string {
  return content
    .replace(/<design_context>[\s\S]*?<\/design_context>/g, '')
    .replace(/<design_context>[\s\S]*/g, '')
    .replace(/\[FINALIZE\]/g, '')
    .trim()
}

export default function ChatMessage({ message }: Props) {
  const isAssistant = message.role === 'assistant'

  return (
    <div className={`flex gap-3 ${isAssistant ? 'justify-start' : 'justify-end'}`}>
      {isAssistant && (
        <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center shrink-0 mt-1">
          <Bot size={16} className="text-accent" />
        </div>
      )}
      <div
        className={`max-w-[75%] rounded-xl px-4 py-3 ${
          isAssistant
            ? 'bg-bg-elevated border border-border/40'
            : 'bg-accent/15 border border-accent/20'
        }`}
      >
        {isAssistant ? (
          <div className="prose prose-sm max-w-none [&_p]:text-text-primary [&_p]:leading-relaxed [&_li]:text-text-primary [&_strong]:text-text-primary">
            <ReactMarkdown
              components={{
                code({ className, children, ...props }) {
                  const match = /language-(\w+)/.exec(className || '')
                  const codeStr = String(children).replace(/\n$/, '')
                  if (match) {
                    return (
                      <div className="rounded-lg overflow-hidden my-3 border border-border/30">
                        <div className="bg-bg-hover px-3 py-1.5 text-[11px] font-mono text-text-tertiary border-b border-border/30">
                          {match[1]}
                        </div>
                        <SyntaxHighlighter
                          style={oneDark}
                          language={match[1]}
                          PreTag="div"
                          customStyle={{
                            margin: 0,
                            borderRadius: 0,
                            background: 'var(--bg-primary)',
                            fontSize: '13px',
                          }}
                        >
                          {codeStr}
                        </SyntaxHighlighter>
                      </div>
                    )
                  }
                  return (
                    <code
                      className={`${className} bg-bg-hover text-accent-light px-1.5 py-0.5 rounded-md text-[13px] font-mono`}
                      {...props}
                    >
                      {children}
                    </code>
                  )
                },
              }}
            >
              {sanitizeContent(message.content)}
            </ReactMarkdown>
            {message.isStreaming && (
              <span className="inline-block w-1.5 h-4 bg-accent rounded-sm animate-pulse ml-0.5" />
            )}
          </div>
        ) : (
          <p className="text-sm text-text-primary whitespace-pre-wrap leading-relaxed">{message.content}</p>
        )}
        {message.createdAt && (
          <div className={`text-[11px] mt-2 ${isAssistant ? 'text-text-muted' : 'text-text-tertiary'}`}>
            {formatTime(message.createdAt)}
          </div>
        )}
      </div>
      {!isAssistant && (
        <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center shrink-0 mt-1">
          <User size={16} className="text-accent" />
        </div>
      )}
    </div>
  )
}
