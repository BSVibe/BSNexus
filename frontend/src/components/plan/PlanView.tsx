import { useCallback, useEffect, useRef, useState } from 'react'
import { usePlanEvents } from '../../hooks/usePlanEvents'
import { usePlanStore } from '../../stores/planStore'
import AgentStatusBar from './AgentStatusBar'
import DetailPanel from './DetailPanel'
import PlanTree from './PlanTree'
import ProposalBanner from './ProposalBanner'

interface PlanViewProps {
  projectId: string
}

const STORAGE_KEY = 'bsnexus.planView.treeWidthPct'
const MIN_PCT = 22
const MAX_PCT = 60
const DEFAULT_PCT = 35

function clampPct(v: number): number {
  return Math.min(MAX_PCT, Math.max(MIN_PCT, v))
}

export default function PlanView({ projectId }: PlanViewProps) {
  // Stream task transitions and agent status updates into the React Query cache.
  usePlanEvents(projectId)

  // Reset selection when switching projects so we never carry over a stale node id.
  const reset = usePlanStore((s) => s.reset)
  useEffect(() => {
    reset()
    return () => reset()
  }, [projectId, reset])

  // Persisted resizable split between PlanTree (left) and DetailPanel (right).
  const containerRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  const [treePct, setTreePct] = useState<number>(() => {
    if (typeof window === 'undefined') return DEFAULT_PCT
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_PCT
    const num = Number.parseFloat(raw)
    return Number.isFinite(num) ? clampPct(num) : DEFAULT_PCT
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    localStorage.setItem(STORAGE_KEY, treePct.toFixed(2))
  }, [treePct])

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      if (rect.width <= 0) return
      const pct = ((e.clientX - rect.left) / rect.width) * 100
      setTreePct(clampPct(pct))
    }
    const onUp = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [])

  return (
    <div className="flex h-full flex-col">
      <AgentStatusBar />
      <ProposalBanner projectId={projectId} />
      <div ref={containerRef} className="flex flex-1 overflow-hidden">
        <div
          className="flex flex-col border-r border-stitch-outline-variant/10"
          style={{ width: `${treePct}%`, minWidth: 240 }}
        >
          <PlanTree projectId={projectId} />
        </div>
        <div
          role="separator"
          aria-orientation="vertical"
          onMouseDown={onMouseDown}
          className="w-1 cursor-col-resize hover:bg-stitch-primary/30 active:bg-stitch-primary/50 transition-colors shrink-0"
        />
        <div className="flex-1 overflow-hidden min-w-0">
          <DetailPanel projectId={projectId} />
        </div>
      </div>
    </div>
  )
}
