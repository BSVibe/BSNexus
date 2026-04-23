import { useEffect, useState } from 'react'
import { Outlet, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import Sidebar from './Sidebar'
import GlobalChat from '../chat/GlobalChat'
import CommandPalette from '../common/CommandPalette'
import { projectsApi, type Project } from '../../api/projects'

const CHAT_COLLAPSED_KEY = 'bsnexus.chat.collapsed'

export default function Layout() {
  const [chatCollapsed, setChatCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem(CHAT_COLLAPSED_KEY) === '1'
  })
  const [paletteOpen, setPaletteOpen] = useState(false)
  const params = useParams<{ projectId?: string }>()

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
      }
    }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [paletteOpen])

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
    </div>
  )
}
