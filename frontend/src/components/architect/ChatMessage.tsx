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

  return (
    <div className={`flex ${isAssistant ? 'justify-start' : 'justify-end'} mb-4`}>
      <div
        className={`max-w-[70%] rounded-lg px-4 py-3 ${
          isAssistant
            ? 'bg-gray-850 text-gray-50 border border-gray-700'
            : 'bg-accent text-white'
        }`}
      >
        {isAssistant ? (
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown
              components={{
                code({ className, children, ...props }) {
                  const match = /language-(\w+)/.exec(className || '')
                  const codeStr = String(children).replace(/\n$/, '')
                  if (match) {
                    return (
                      <SyntaxHighlighter
                        style={oneDark}
                        language={match[1]}
                        PreTag="div"
                      >
                        {codeStr}
                      </SyntaxHighlighter>
                    )
                  }
                  return <code className={`${className} bg-gray-800 text-gray-300 px-1 py-0.5 rounded`} {...props}>{children}</code>
                },
              }}
            >
              {sanitizeContent(message.content)}
            </ReactMarkdown>
            {message.isStreaming && (
              <span className="inline-block w-2 h-4 bg-accent animate-pulse ml-0.5" />
            )}
          </div>
        ) : (
          <p className="text-sm whitespace-pre-wrap">{message.content}</p>
        )}
        {message.createdAt && (
          <div className={`text-xs mt-1 ${isAssistant ? 'text-gray-500' : 'text-white/70'}`}>
            {formatTime(message.createdAt)}
          </div>
        )}
      </div>
    </div>
  )
}
