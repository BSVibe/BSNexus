import { useState, useRef, useEffect } from 'react'

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
    <div className="bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-2xl shadow-2xl p-2 transition-all focus-within:border-stitch-primary/40">
      {/* Toolbar row */}
      <div className="flex items-center px-4 py-2 border-b border-stitch-outline-variant/10 space-x-4">
        <div className="flex items-center space-x-2 text-[10px] font-bold uppercase tracking-widest text-stitch-primary">
          <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>model_training</span>
          <span>Architect</span>
        </div>
        <div className="h-4 w-[1px] bg-stitch-outline-variant/20" />
        <button className="text-text-secondary hover:text-stitch-primary transition-colors">
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>attach_file</span>
        </button>
        <button className="text-text-secondary hover:text-stitch-primary transition-colors">
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>image</span>
        </button>
        <div className="flex-1" />
        <span className="text-[10px] text-text-muted font-mono">MD SUPPORTED</span>
      </div>
      {/* Input row */}
      <div className="relative flex items-end p-2">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Orchestrate your next move..."
          disabled={disabled}
          rows={1}
          className="w-full bg-transparent border-none focus:ring-0 text-sm py-3 px-2 resize-none max-h-48 placeholder:text-text-muted text-text-primary"
        />
        <button
          type="button"
          onClick={handleSubmit}
          disabled={disabled || !input.trim()}
          className="ml-2 w-10 h-10 rounded-xl bg-stitch-primary-container hover:bg-stitch-primary text-white flex items-center justify-center transition-all shadow-[0px_0px_20px_rgba(77,142,255,0.3)] hover:scale-105 active:scale-95 disabled:opacity-30"
        >
          <span className="material-symbols-outlined" style={{ fontVariationSettings: "'FILL' 1" }}>send</span>
        </button>
      </div>
    </div>
  )
}
