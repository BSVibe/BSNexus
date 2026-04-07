import type { ReactNode } from 'react'

interface HeaderProps {
  title: ReactNode
  action?: ReactNode
}

export default function Header({ title, action }: HeaderProps) {
  return (
    <header className="flex justify-between items-center w-full px-8 sticky top-0 z-10 bg-stitch-surface-lowest/80 backdrop-blur-xl h-16 border-b border-stitch-surface-container/50 shadow-[0_0_20px_rgba(133,173,255,0.04)]">
      <div className="flex items-center gap-8">
        <h2 className="text-lg font-bold tracking-tight text-white">{title}</h2>
      </div>
      <div className="flex items-center gap-4">
        {action}
        <button className="p-2 text-text-secondary hover:text-stitch-primary transition-colors">
          <span className="material-symbols-outlined">notifications</span>
        </button>
      </div>
    </header>
  )
}
