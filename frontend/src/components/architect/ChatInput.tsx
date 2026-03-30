import { useState, useRef, useEffect } from 'react'
import { Send } from 'lucide-react'

interface Props {
  onSend: (message: string) => void
  disabled?: boolean
}

export default function ChatInput({ onSend, disabled }: Props) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`
    }
  }, [input])

  const handleSubmit = () => {
    if (!input.trim() || disabled) return
    onSend(input.trim())
    setInput('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="relative">
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Describe your project idea..."
        disabled={disabled}
        rows={1}
        className="w-full resize-none rounded-xl bg-bg-elevated border border-border/50 pl-4 pr-12 py-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50 focus:ring-1 focus:ring-accent/30 disabled:opacity-50 transition-colors"
      />
      <button
        type="button"
        onClick={handleSubmit}
        disabled={disabled || !input.trim()}
        className="absolute right-2 bottom-2 p-2 rounded-lg bg-accent hover:bg-accent-light text-gray-50 disabled:opacity-30 disabled:hover:bg-accent transition-colors"
      >
        <Send size={16} />
      </button>
    </div>
  )
}
