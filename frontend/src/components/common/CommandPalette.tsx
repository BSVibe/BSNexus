'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useRouter } from 'next/navigation'

import { I } from '../../lib/icons'
import { truncId } from '../../lib/fmt'
import { statusTone } from '../../lib/tone'
import { StatusDot } from './Badge'
import type { Project } from '../../api/projects'

interface CommandPaletteProps {
  projects: Project[]
  onClose: () => void
}

type ItemKind = 'action' | 'nav' | 'project'

interface Item {
  kind: ItemKind
  label: string
  sub?: string
  icon: React.ReactNode
  run: () => void
}

export default function CommandPalette({ projects, onClose }: CommandPaletteProps) {
  const router = useRouter()
  const t = useTranslations('nexus.palette')
  const navigate = (href: string) => router.push(href)
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 10)
    return () => clearTimeout(t)
  }, [])

  const items: Item[] = useMemo(() => {
    const base: Item[] = [
      {
        kind: 'action',
        label: t('newProject'),
        icon: <I.Plus size={14} />,
        run: () => navigate('/dashboard?new=1'),
      },
      {
        kind: 'nav',
        label: t('dashboard'),
        icon: <I.Home size={14} />,
        run: () => navigate('/dashboard'),
      },
      {
        kind: 'nav',
        label: t('settingsIntegrations'),
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
  }, [q, projects, navigate, t])

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

  const kindLabel = (kind: ItemKind): string => {
    if (kind === 'action') return t('kindAction')
    if (kind === 'nav') return t('kindNav')
    return t('kindProject')
  }

  return (
    <div className="cmd-mask" onClick={onClose}>
      <div className="cmd" onClick={(e) => e.stopPropagation()} onKeyDown={onKey}>
        <input
          ref={inputRef}
          className="cmd-input"
          placeholder={t('placeholder')}
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setIdx(0)
          }}
        />
        <div className="cmd-list">
          {items.length === 0 && (
            <div style={{ padding: 16, color: 'var(--text-tertiary)', fontSize: 13 }}>
              {t('noMatches')}
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
              <span className="cmd-kind">{kindLabel(it.kind)}</span>
            </div>
          ))}
        </div>
        <div className="cmd-hint">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> {t('navigate')}
          </span>
          <span>
            <kbd>↵</kbd> {t('openItem')}
          </span>
          <span>
            <kbd>esc</kbd> {t('closePalette')}
          </span>
          <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>⌘K</span>
        </div>
      </div>
    </div>
  )
}
