import { useState } from 'react'
import type { DesignSession } from '../../types/architect'
import { Button } from '../common'

interface Props {
  sessions: DesignSession[]
  activeSessionId: string | null
  onSelect: (sessionId: string) => void
  onNew: () => void
  onDelete?: (sessionId: string) => void
  onBatchDelete?: (sessionIds: string[]) => void
}

function getSessionLabel(session: DesignSession): string {
  if (session.name) return session.name
  if (session.messages.length > 0) {
    const firstUserMsg = session.messages.find((m) => m.role === 'user')
    if (firstUserMsg) return firstUserMsg.content.slice(0, 40)
  }
  return 'New Session'
}

export default function SessionList({ sessions, activeSessionId, onSelect, onNew, onDelete, onBatchDelete }: Props) {
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelectedIds(new Set())
  }

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const selectAll = () => {
    setSelectedIds(new Set(sessions.map((s) => s.id)))
  }

  const handleBatchDelete = () => {
    if (onBatchDelete && selectedIds.size > 0) {
      onBatchDelete([...selectedIds])
      exitSelectMode()
    }
  }

  return (
    <div className="w-[260px] border-r border-stitch-outline-variant/10 bg-stitch-surface-low flex flex-col h-full">
      <div className="p-4 space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-white font-bold text-sm">Sessions</h2>
          <div className="flex items-center gap-1.5">
            {!selectMode && sessions.length > 0 && (
              <button
                onClick={() => setSelectMode(true)}
                className="p-1 rounded text-text-tertiary hover:text-white hover:bg-stitch-surface-container transition-colors"
                title="Select mode"
              >
                <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>checklist</span>
              </button>
            )}
            <button
              onClick={onNew}
              className="bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container px-3 py-1.5 rounded-md text-xs font-bold shadow-lg shadow-stitch-primary/20 flex items-center gap-1"
            >
              <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>add</span>
              New
            </button>
          </div>
        </div>
        {selectMode && (
          <div className="flex items-center gap-1.5">
            <Button variant="secondary" size="sm" className="!text-xs !px-2 !py-0.5" onClick={selectAll}>All</Button>
            {selectedIds.size > 0 && (
              <>
                <span className="text-xs text-text-secondary bg-stitch-surface-container px-1.5 py-0.5 rounded-full">
                  {selectedIds.size} selected
                </span>
                <Button
                  size="sm"
                  className="!bg-stitch-error-container hover:!bg-stitch-error-container/80 !text-stitch-error !text-xs !px-2 !py-0.5"
                  onClick={handleBatchDelete}
                >
                  Delete
                </Button>
              </>
            )}
            <Button variant="secondary" size="sm" className="!text-xs !px-2 !py-0.5" onClick={exitSelectMode}>Cancel</Button>
          </div>
        )}
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-1">
        {sessions.map((session) => {
          const isActive = activeSessionId === session.id
          const isSelected = selectedIds.has(session.id)
          return (
            <div
              key={session.id}
              onClick={selectMode ? () => toggleSelect(session.id) : undefined}
              className={`relative group rounded-lg transition-colors ${
                selectMode ? 'cursor-pointer' : ''
              } ${
                isSelected
                  ? 'bg-stitch-primary/10 border-l-2 border-stitch-primary'
                  : isActive
                    ? 'bg-stitch-primary/10 border-l-2 border-stitch-primary'
                    : 'hover:bg-stitch-surface-container border-l-2 border-transparent'
              }`}
            >
              {selectMode && (
                <div
                  className={`absolute top-2.5 left-1.5 w-4 h-4 rounded border flex items-center justify-center transition-colors z-10 ${
                    isSelected ? 'border-stitch-primary bg-stitch-primary' : 'border-stitch-outline-variant'
                  }`}
                >
                  {isSelected && (
                    <span className="material-symbols-outlined text-stitch-on-primary" style={{ fontSize: '12px' }}>check</span>
                  )}
                </div>
              )}
              {selectMode ? (
                <div className="w-full text-left px-3 py-2.5 text-sm pl-7">
                  <div className={`truncate text-text-primary ${isSelected ? 'font-semibold' : ''}`}>
                    {getSessionLabel(session)}
                  </div>
                  <div className="text-xs text-text-tertiary mt-0.5">
                    {new Date(session.created_at).toLocaleDateString()}
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => onSelect(session.id)}
                  className="w-full text-left px-3 py-2.5 text-sm pr-8"
                >
                  <div className={`truncate ${isActive ? 'font-semibold text-stitch-primary' : 'text-text-primary'}`}>
                    {getSessionLabel(session)}
                  </div>
                  <div className="text-xs text-text-tertiary mt-0.5">
                    {new Date(session.created_at).toLocaleDateString()}
                  </div>
                </button>
              )}
              {onDelete && !selectMode && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    onDelete(session.id)
                  }}
                  className="absolute top-2 right-2 p-1 rounded-md text-text-tertiary hover:text-stitch-error hover:bg-stitch-error-container/10 opacity-0 group-hover:opacity-100 transition-all"
                  title="Delete session"
                >
                  <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>close</span>
                </button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
