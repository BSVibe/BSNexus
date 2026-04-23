import { useEffect, useMemo, useRef, useState } from 'react'

import BsdArtboard from './BsdArtboard'
import type { BsdFrame, BsdTokens } from '../../lib/bsd-types'

interface BsdCanvasProps {
  frames: BsdFrame[]
  tokens: BsdTokens
}

interface LaidOutFrame {
  id: string
  frame: BsdFrame
  scale: number
  w: number
  h: number
  x: number
  y: number
}

const MAX_ROW_W = 2400
const GAP = 56
const LABEL_H = 28
const MAX_W = 720

/**
 * Free-pan canvas rendering of every frame in a .bsd document.
 * - Plain drag/ wheel → pan.
 * - ⌘/Ctrl + wheel → zoom around the cursor.
 * - "Fit" button → recentres everything in view.
 */
export default function BsdCanvas({ frames, tokens }: BsdCanvasProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 40, y: 40 })
  const [dragging, setDragging] = useState(false)

  const layout = useMemo(() => layoutFrames(frames), [frames])

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      if (e.ctrlKey || e.metaKey) {
        const rect = el.getBoundingClientRect()
        const cx = e.clientX - rect.left
        const cy = e.clientY - rect.top
        setZoom((z) => {
          const nz = Math.max(0.15, Math.min(3, z * (1 - e.deltaY * 0.0015)))
          setPan((p) => ({
            x: cx - ((cx - p.x) * nz) / z,
            y: cy - ((cy - p.y) * nz) / z,
          }))
          return nz
        })
      } else {
        setPan((p) => ({ x: p.x - e.deltaX, y: p.y - e.deltaY }))
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const onPointerDown: React.PointerEventHandler<HTMLDivElement> = (e) => {
    const target = e.target as HTMLElement
    const isEmpty = target === e.currentTarget || target.dataset.canvasBg === '1'
    if (e.button !== 1 && !e.shiftKey && !isEmpty) return
    e.preventDefault()
    setDragging(true)
    const sx = e.clientX
    const sy = e.clientY
    const p0 = { ...pan }
    const move = (ev: PointerEvent) =>
      setPan({ x: p0.x + (ev.clientX - sx), y: p0.y + (ev.clientY - sy) })
    const up = () => {
      setDragging(false)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const fit = () => {
    const el = wrapRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const maxX = Math.max(...layout.items.map((it) => it.x + it.w), 800)
    const s = Math.min(
      rect.width / (maxX + 80),
      rect.height / (layout.totalH + 80),
      1,
    )
    setZoom(s)
    setPan({ x: (rect.width - maxX * s) / 2, y: 40 })
  }

  return (
    <div
      ref={wrapRef}
      onPointerDown={onPointerDown}
      style={{
        position: 'relative',
        flex: 1,
        minHeight: 0,
        overflow: 'hidden',
        cursor: dragging ? 'grabbing' : 'grab',
        background: 'var(--bg-base)',
        backgroundImage:
          'radial-gradient(circle, rgba(255,255,255,0.06) 1px, transparent 1px)',
        backgroundSize: `${24 * zoom}px ${24 * zoom}px`,
        backgroundPosition: `${pan.x}px ${pan.y}px`,
      }}
    >
      <div data-canvas-bg="1" style={{ position: 'absolute', inset: 0 }} />
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          transformOrigin: '0 0',
          pointerEvents: 'none',
        }}
      >
        {layout.items.map((it) => (
          <div
            key={it.id}
            style={{
              position: 'absolute',
              left: it.x,
              top: it.y,
              pointerEvents: 'auto',
            }}
          >
            <BsdArtboard frame={it.frame} tokens={tokens} w={it.w} h={it.h} />
          </div>
        ))}
      </div>

      <div
        style={{
          position: 'absolute',
          bottom: 12,
          right: 12,
          display: 'flex',
          gap: 0,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--r-md)',
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          overflow: 'hidden',
        }}
      >
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          onClick={() => setZoom((z) => Math.max(0.15, z - 0.1))}
          style={{ borderRadius: 0 }}
        >
          −
        </button>
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          onClick={fit}
          style={{
            minWidth: 60,
            borderRadius: 0,
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
          }}
        >
          {Math.round(zoom * 100)}%
        </button>
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          onClick={() => setZoom((z) => Math.min(3, z + 0.1))}
          style={{ borderRadius: 0 }}
        >
          +
        </button>
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          onClick={fit}
          style={{ borderRadius: 0 }}
          title="Fit all"
        >
          Fit
        </button>
      </div>

      <div
        style={{
          position: 'absolute',
          bottom: 12,
          left: 12,
          fontSize: 10,
          color: 'var(--text-tertiary)',
          padding: '4px 8px',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--r-sm)',
          pointerEvents: 'none',
        }}
      >
        Drag to pan · <span className="mono">⌃</span>/<span className="mono">⌘</span>+wheel to zoom
      </div>
    </div>
  )
}

function layoutFrames(frames: BsdFrame[]): { items: LaidOutFrame[]; totalH: number } {
  const sized = frames.map<LaidOutFrame>((f) => {
    const scale = Math.min(1, MAX_W / f.w)
    return {
      id: f.id,
      frame: f,
      scale,
      w: f.w * scale,
      h: f.h * scale,
      x: 0,
      y: 0,
    }
  })
  const rows: { row: LaidOutFrame[]; rowH: number }[] = []
  let row: LaidOutFrame[] = []
  let rowW = 0
  let rowH = 0
  sized.forEach((it) => {
    if (row.length && rowW + GAP + it.w > MAX_ROW_W) {
      rows.push({ row, rowH })
      row = []
      rowW = 0
      rowH = 0
    }
    if (row.length) rowW += GAP
    it.x = rowW
    rowW += it.w
    rowH = Math.max(rowH, it.h)
    row.push(it)
  })
  if (row.length) rows.push({ row, rowH })

  let y = 0
  rows.forEach((r) => {
    r.row.forEach((it) => {
      it.y = y
    })
    y += r.rowH + GAP + LABEL_H
  })
  return { items: sized, totalH: y }
}
