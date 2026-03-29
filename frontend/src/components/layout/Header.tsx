import type { ReactNode } from 'react'

interface HeaderProps {
  title: string
  action?: ReactNode
}

export default function Header({ title, action }: HeaderProps) {
  return (
    <header className="bg-bg-surface/70 backdrop-blur-md border-b border-border/40 px-8 py-4 flex items-center justify-between sticky top-0 z-10">
      <h1 className="text-xl font-bold text-text-primary tracking-tight">{title}</h1>
      {action && <div className="flex items-center gap-3">{action}</div>}
    </header>
  )
}
