'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useParams, usePathname } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'

import Sidebar from './Sidebar'
import GlobalChat from '../chat/GlobalChat'
import CommandPalette from '../common/CommandPalette'
import { projectsApi, type Project } from '../../api/projects'
import { useProjectEvents } from '../../hooks/useProjectEvents'

const CHAT_COLLAPSED_KEY = 'bsnexus.chat.collapsed'

export default function Layout({ children }: { children: React.ReactNode }) {
  const t = useTranslations('nexus.layout')
  const [chatCollapsed, setChatCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem(CHAT_COLLAPSED_KEY) === '1'
  })
  const [paletteOpen, setPaletteOpen] = useState(false)
  // Phase B Batch 2: mobile drawer state. Hamburger toggles the sidebar
  // off-canvas under 768px. Backdrop tap closes both panels.
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [mobileChatOpen, setMobileChatOpen] = useState(false)
  const params = useParams<{ projectId?: string | string[] }>()
  const pathname = usePathname()
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

  // Phase B: close mobile sidebar drawer on any nav. Sidebar items use
  // `router.push()` from button onClicks; we react to pathname changes.
  useEffect(() => {
    setMobileSidebarOpen(false)
    setMobileChatOpen(false)
  }, [pathname])

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

  const closeMobilePanels = () => {
    setMobileSidebarOpen(false)
    setMobileChatOpen(false)
  }

  // CSS-class composition keeps the main render tree pure — no extra
  // wrappers are introduced, so existing tests anchored on `.app .sb .mn`
  // continue to work.
  const appClass = [
    'app',
    chatCollapsed ? 'chat-collapsed' : '',
    mobileSidebarOpen ? 'app--mobile-sidebar-open' : '',
    mobileChatOpen ? 'app--mobile-chat-open' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={appClass}>
      {/* Phase B Batch 2 — mobile hamburger. Visible only under 768px via CSS. */}
      <button
        type="button"
        aria-label={t('openNavigation')}
        aria-expanded={mobileSidebarOpen}
        className="app__hamburger"
        onClick={() => setMobileSidebarOpen(true)}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      <Sidebar projects={projects} onOpenPalette={() => setPaletteOpen(true)} />
      <main className="mn">{children}</main>
      <GlobalChat
        projects={projects}
        currentProject={currentProject}
        collapsed={chatCollapsed}
        onToggleCollapsed={() => setChatCollapsed((c) => !c)}
      />

      {/* Mobile-only backdrop — closes either drawer on tap. */}
      {(mobileSidebarOpen || mobileChatOpen) && (
        <div
          data-testid="bsnexus-mobile-backdrop"
          className="app--mobile-backdrop"
          role="presentation"
          onClick={closeMobilePanels}
        />
      )}

      {paletteOpen && (
        <CommandPalette projects={projects} onClose={() => setPaletteOpen(false)} />
      )}
    </div>
  )
}
