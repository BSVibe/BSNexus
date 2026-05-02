'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'

import Sidebar from './Sidebar'
import GlobalChat from '../chat/GlobalChat'
import CommandPalette from '../common/CommandPalette'
import { projectsApi, type Project } from '../../api/projects'
import { useProjectEvents } from '../../hooks/useProjectEvents'

const CHAT_COLLAPSED_KEY = 'bsnexus.chat.collapsed'

export default function Layout({ children }: { children: React.ReactNode }) {
  const [chatCollapsed, setChatCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem(CHAT_COLLAPSED_KEY) === '1'
  })
  const [paletteOpen, setPaletteOpen] = useState(false)
  // Phase B Batch 2: mobile drawer state.
  // Sidebar drawer is owned by `@bsvibe/layout`'s ResponsiveSidebar (it
  // ships its own hamburger, backdrop and Escape handling). We keep the
  // chat-rail drawer state local because GlobalChat does not yet share
  // that contract.
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [mobileChatOpen, setMobileChatOpen] = useState(false)
  const params = useParams<{ projectId?: string | string[] }>()
  const rawProjectId = params?.projectId
  const projectId = Array.isArray(rawProjectId) ? rawProjectId[0] : rawProjectId

  useEffect(() => {
    if (typeof window === 'undefined') return
    localStorage.setItem(CHAT_COLLAPSED_KEY, chatCollapsed ? '1' : '0')
  }, [chatCollapsed])

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen(true)
      } else if ((e.metaKey || e.ctrlKey) && e.key === '/') {
        e.preventDefault()
        setChatCollapsed((c) => !c)
      } else if (e.key === 'Escape') {
        if (paletteOpen) setPaletteOpen(false)
        setMobileSidebarOpen(false)
        setMobileChatOpen(false)
      }
    }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [paletteOpen])

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  const currentProject = projectId
    ? projects.find((p) => p.id === projectId) ?? null
    : null

  // Subscribe to per-project SSE so chat / deliverables / decisions
  // update in real time. Only the current project route streams; the
  // chat rail still polls other projects (it sees ALL projects via
  // ``useQueries`` for cross-project visibility).
  useProjectEvents(projectId ?? null)

  const closeMobileChat = () => {
    setMobileChatOpen(false)
  }

  // CSS-class composition keeps the main render tree pure. The sidebar
  // drawer styling now ships from `@bsvibe/layout`; only the chat-rail
  // drawer needs a state hook on `.app`.
  const appClass = [
    'app',
    chatCollapsed ? 'chat-collapsed' : '',
    mobileChatOpen ? 'app--mobile-chat-open' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={appClass}>
      <Sidebar
        projects={projects}
        onOpenPalette={() => setPaletteOpen(true)}
        open={mobileSidebarOpen}
        onOpenChange={setMobileSidebarOpen}
      />
      <main className="mn">{children}</main>
      <GlobalChat
        projects={projects}
        currentProject={currentProject}
        collapsed={chatCollapsed}
        onToggleCollapsed={() => setChatCollapsed((c) => !c)}
      />

      {/* Mobile-only backdrop — closes the chat drawer on tap.
          The sidebar drawer ships its own backdrop via @bsvibe/layout. */}
      {mobileChatOpen && (
        <div
          data-testid="bsnexus-mobile-backdrop"
          className="app--mobile-backdrop"
          role="presentation"
          onClick={closeMobileChat}
        />
      )}

      {paletteOpen && (
        <CommandPalette projects={projects} onClose={() => setPaletteOpen(false)} />
      )}
    </div>
  )
}
