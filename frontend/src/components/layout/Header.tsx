import type { ReactNode } from 'react'

interface HeaderProps {
  title: string
  action?: ReactNode
}

export default function Header({ title, action }: HeaderProps) {
  return (
    <header className="flex justify-between items-center w-full px-8 sticky top-0 z-10 bg-stitch-surface/80 backdrop-blur-md h-16 shadow-[0px_4px_20px_rgba(0,0,0,0.3)]">
      <div className="flex items-center gap-8">
        <h2 className="text-lg font-black uppercase tracking-widest text-white">{title}</h2>
      </div>
      <div className="flex items-center gap-4">
        {action}
        <button className="p-2 text-text-secondary hover:text-white transition-colors">
          <span className="material-symbols-outlined">notifications</span>
        </button>
      </div>
    </header>
  )
}
