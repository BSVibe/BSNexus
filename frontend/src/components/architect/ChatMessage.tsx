import ReactMarkdown from 'react-markdown'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { ChatMessage as ChatMessageType } from '../../stores/architectStore'

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

  if (!isAssistant) {
    // User message: right-aligned, dark bg, rounded
    return (
      <div className="flex justify-end w-full">
        <div className="max-w-[80%] bg-[#262626] rounded-2xl rounded-tr-none px-5 py-4 shadow-lg">
          <p className="text-sm leading-relaxed text-text-primary whitespace-pre-wrap">{message.content}</p>
          {message.createdAt && (
            <div className="text-[11px] mt-2 text-text-muted">{formatTime(message.createdAt)}</div>
          )}
        </div>
      </div>
    )
  }

  // AI message: left-aligned, bordered, with icon header
  return (
    <div className="flex justify-start w-full">
      <div className="max-w-[90%] bg-[#171717] border border-stitch-primary/10 rounded-2xl rounded-tl-none px-6 py-6 shadow-xl space-y-4">
        <div className="flex items-center space-x-3 mb-2">
          <div className="w-6 h-6 rounded bg-stitch-primary/20 flex items-center justify-center">
            <span className="material-symbols-outlined text-stitch-primary" style={{ fontSize: '14px', fontVariationSettings: "'FILL' 1" }}>bolt</span>
          </div>
          <span className="text-xs font-bold tracking-widest text-stitch-primary uppercase">Architect</span>
          {message.createdAt && (
            <span className="text-[10px] text-text-muted ml-auto">{formatTime(message.createdAt)}</span>
          )}
        </div>
        <div className="text-sm leading-relaxed text-text-secondary prose prose-sm max-w-none [&_p]:text-text-secondary [&_p]:leading-relaxed [&_li]:text-text-secondary [&_strong]:text-white [&_h1]:text-white [&_h2]:text-white [&_h3]:text-white [&_h4]:text-white">
          <ReactMarkdown
            components={{
              code({ className, children, ...props }) {
                const match = /language-(\w+)/.exec(className || '')
                const codeStr = String(children).replace(/\n$/, '')
                if (match) {
                  return (
                    <div className="bg-[#1f1f1f] rounded-lg overflow-hidden my-3 border border-stitch-outline-variant/10">
                      <div className="flex items-center justify-between px-4 py-2 border-b border-stitch-outline-variant/20">
                        <span className="text-text-muted text-[10px] uppercase tracking-widest">{match[1]}</span>
                        <span className="material-symbols-outlined text-text-muted" style={{ fontSize: '14px' }}>content_copy</span>
                      </div>
                      <SyntaxHighlighter
                        style={oneDark}
                        language={match[1]}
                        PreTag="div"
                        customStyle={{
                          margin: 0,
                          borderRadius: 0,
                          background: '#1f1f1f',
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
                    className={`${className} bg-stitch-surface-container text-stitch-primary px-1.5 py-0.5 rounded-md text-[13px] font-mono`}
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
            <span className="inline-block w-0.5 h-[1.2em] bg-stitch-primary ml-0.5 align-middle animate-cursor-blink" />
          )}
        </div>
      </div>
    </div>
  )
}
