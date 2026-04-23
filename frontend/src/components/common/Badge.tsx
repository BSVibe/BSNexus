import type { ReactNode } from 'react'

import { accentHex, type Tone } from '../../lib/tone'

interface BadgeProps {
  children: ReactNode
  tone?: Tone
  dot?: boolean
  square?: boolean
  title?: string
}

export function Badge({ children, tone = 'gray', dot = false, square = false, title }: BadgeProps) {
  return (
    <span className={`badge badge-${tone} ${square ? 'badge-sq' : ''}`} title={title}>
      {dot && <span className="dot" style={{ background: accentHex[tone] }} />}
      {children}
    </span>
  )
}

export function StatusDot({ tone, size = 8 }: { tone: Tone; size?: number }) {
  return (
    <span
      className="dot"
      style={{
        background: accentHex[tone],
        width: size,
        height: size,
        borderRadius: 99,
        display: 'inline-block',
        flex: 'none',
      }}
    />
  )
}

export type { BadgeProps }
