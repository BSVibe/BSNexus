'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'

import Sidebar from './Sidebar'
import CommandPalette from '../common/CommandPalette'
import { projectsApi, type Project } from '../../api/projects'
import { useProjectEvents } from '../../hooks/useProjectEvents'

export default function Layout({ children }: { children: React.ReactNode }) {
  const [paletteOpen, setPaletteOpen] = useState(false)
  // Sidebar drawer is owned by `@bsvibe/layout`'s ResponsiveSidebar (it
  // ships its own hamburger, backdrop and Escape handling).
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const params = useParams<{ projectId?: string | string[] }>()
  const rawProjectId = params?.projectId
  const projectId = Array.isArray(rawProjectId) ? rawProjectId[0] : rawProjectId

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen(true)
      } else if (e.key === 'Escape') {
        if (paletteOpen) setPaletteOpen(false)
        setMobileSidebarOpen(false)
      }
    }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [paletteOpen])

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  // Subscribe to per-project SSE so deliverables / decisions / brief
  // update in real time.
  useProjectEvents(projectId ?? null)

  return (
    <div className="app">
      <Sidebar
        projects={projects}
        onOpenPalette={() => setPaletteOpen(true)}
        open={mobileSidebarOpen}
        onOpenChange={setMobileSidebarOpen}
      />
      <main className="mn">{children}</main>

      {paletteOpen && (
        <CommandPalette projects={projects} onClose={() => setPaletteOpen(false)} />
      )}
    </div>
  )
}
