import { useEffect, useState } from 'react'
import { Outlet, useLocation, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import Sidebar from './Sidebar'
import Topbar from './Topbar'
import GlobalChat from '../chat/GlobalChat'
import CommandPalette from '../common/CommandPalette'
import Inspector from '../inside/Inspector'
import { projectsApi, type Project } from '../../api/projects'

const CHAT_COLLAPSED_KEY = 'bsnexus.chat.collapsed'

export default function Layout() {
  const [chatCollapsed, setChatCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem(CHAT_COLLAPSED_KEY) === '1'
  })
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [inspectorFocus, setInspectorFocus] = useState<{ requestId?: string } | null>(null)
  const location = useLocation()
  const params = useParams<{ projectId?: string }>()

  useEffect(() => {
    if (typeof window === 'undefined') return
    localStorage.setItem(CHAT_COLLAPSED_KEY, chatCollapsed ? '1' : '0')
  }, [chatCollapsed])

  // keyboard shortcuts
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const typing =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable)
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen(true)
      } else if ((e.metaKey || e.ctrlKey) && e.key === '/') {
        e.preventDefault()
        setChatCollapsed((c) => !c)
      } else if (!typing && e.key.toLowerCase() === 'e') {
        if (location.pathname.startsWith('/projects/')) {
          e.preventDefault()
          setInspectorOpen((o) => !o)
        }
      } else if (e.key === 'Escape') {
        if (paletteOpen) setPaletteOpen(false)
        if (inspectorOpen) setInspectorOpen(false)
      }
    }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [location.pathname, paletteOpen, inspectorOpen])

  useEffect(() => {
    const openInspector = (e: Event) => {
      const ce = e as CustomEvent<{ requestId?: string }>
      setInspectorFocus(ce.detail ?? null)
      setInspectorOpen(true)
    }
    document.addEventListener('bsn:open-inspector', openInspector as EventListener)
    return () =>
      document.removeEventListener('bsn:open-inspector', openInspector as EventListener)
  }, [])

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  const currentProject = params.projectId
    ? projects.find((p) => p.id === params.projectId) ?? null
    : null

  return (
    <div className={`app ${chatCollapsed ? 'chat-collapsed' : ''}`}>
      <Sidebar projects={projects} onOpenPalette={() => setPaletteOpen(true)} />
      <Topbar
        currentProject={currentProject}
        onOpenInspector={() => setInspectorOpen(true)}
        inspectorOpen={inspectorOpen}
        onOpenPalette={() => setPaletteOpen(true)}
      />
      <main className="mn">
        <Outlet />
      </main>
      <GlobalChat
        projects={projects}
        currentProject={currentProject}
        collapsed={chatCollapsed}
        onToggleCollapsed={() => setChatCollapsed((c) => !c)}
      />

      {paletteOpen && (
        <CommandPalette projects={projects} onClose={() => setPaletteOpen(false)} />
      )}
      {inspectorOpen && (
        <Inspector
          focus={inspectorFocus}
          onClose={() => {
            setInspectorOpen(false)
            setInspectorFocus(null)
          }}
        />
      )}
    </div>
  )
}
