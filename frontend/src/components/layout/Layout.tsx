import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import HelpButton from '../help/HelpButton'

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="flex h-screen overflow-hidden text-text-primary bg-stitch-surface">
      <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <main className="flex-1 flex flex-col min-w-0 bg-stitch-surface overflow-hidden">
        {/* Hamburger - mobile only */}
        <button
          className="md:hidden fixed top-4 left-4 z-30 p-2 rounded-lg bg-stitch-surface-low text-text-secondary"
          onClick={() => setSidebarOpen(true)}
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <div className="flex-1 flex flex-col overflow-auto">
          <Outlet />
        </div>
      </main>
      <HelpButton />
    </div>
  )
}
