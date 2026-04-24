import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { I } from '../../lib/icons'
import { truncId } from '../../lib/fmt'
import { statusTone } from '../../lib/tone'
import { StatusDot } from './Badge'
import type { Project } from '../../api/projects'

interface CommandPaletteProps {
  projects: Project[]
  onClose: () => void
}

interface Item {
  kind: 'action' | 'nav' | 'project'
  label: string
  sub?: string
  icon: React.ReactNode
  run: () => void
}

export default function CommandPalette({ projects, onClose }: CommandPaletteProps) {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    // Only the imperative focus call belongs in an effect — q/idx
    // already start at their defaults via useState initializer.
    const t = setTimeout(() => inputRef.current?.focus(), 10)
    return () => clearTimeout(t)
  }, [])

  const items: Item[] = useMemo(() => {
    const base: Item[] = [
      {
        kind: 'action',
        label: 'New project',
        icon: <I.Plus size={14} />,
        run: () => navigate('/dashboard?new=1'),
      },
      {
        kind: 'nav',
        label: 'Dashboard',
        icon: <I.Home size={14} />,
        run: () => navigate('/dashboard'),
      },
      {
        kind: 'nav',
        label: 'Settings · Integrations',
        icon: <I.Settings size={14} />,
        run: () => navigate('/settings'),
      },
      ...projects.map<Item>((p) => ({
        kind: 'project',
        label: p.name,
        sub: truncId(p.id),
        icon: <StatusDot tone={statusTone(p.status)} size={6} />,
        run: () => navigate(`/projects/${p.id}`),
      })),
    ]
    if (!q) return base
    const needle = q.toLowerCase()
    return base.filter((i) => i.label.toLowerCase().includes(needle))
  }, [q, projects, navigate])

  const run = (it: Item | undefined) => {
    if (!it) return
    onClose()
    it.run()
  }

  const onKey: React.KeyboardEventHandler<HTMLDivElement> = (e) => {
    if (e.key === 'Escape') {
      onClose()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setIdx((i) => Math.min(items.length - 1, i + 1))
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setIdx((i) => Math.max(0, i - 1))
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      run(items[idx])
    }
  }

  return (
    <div className="cmd-mask" onClick={onClose}>
      <div className="cmd" onClick={(e) => e.stopPropagation()} onKeyDown={onKey}>
        <input
          ref={inputRef}
          className="cmd-input"
          placeholder="Search projects, actions…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setIdx(0)
          }}
        />
        <div className="cmd-list">
          {items.length === 0 && (
            <div style={{ padding: 16, color: 'var(--text-tertiary)', fontSize: 13 }}>
              No matches.
            </div>
          )}
          {items.map((it, i) => (
            <div
              key={i}
              className={`cmd-item ${i === idx ? 'active' : ''}`}
              onMouseEnter={() => setIdx(i)}
              onClick={() => run(it)}
            >
              <span style={{ color: 'var(--gray-400)' }}>{it.icon}</span>
              <span style={{ flex: 1 }}>{it.label}</span>
              {it.sub && (
                <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  {it.sub}
                </span>
              )}
              <span className="cmd-kind">{it.kind}</span>
            </div>
          ))}
        </div>
        <div className="cmd-hint">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> navigate
          </span>
          <span>
            <kbd>↵</kbd> open
          </span>
          <span>
            <kbd>esc</kbd> close
          </span>
          <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>⌘K</span>
        </div>
      </div>
    </div>
  )
}
