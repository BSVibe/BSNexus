import { useState } from 'react'
import type { DesignSession } from '../../types/architect'
import { Button } from '../common'
import { ListChecks } from 'lucide-react'

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
    <div className="w-[260px] border-r border-border bg-bg-surface flex flex-col h-full">
      <div className="p-4 space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-text-primary font-semibold text-sm">Sessions</h2>
          <div className="flex items-center gap-1.5">
            {!selectMode && sessions.length > 0 && (
              <button
                onClick={() => setSelectMode(true)}
                className="p-1 rounded text-text-tertiary hover:text-text-primary hover:bg-bg-hover transition-colors"
                title="Select mode"
              >
                <ListChecks size={14} />
              </button>
            )}
            <Button variant="primary" size="sm" onClick={onNew}>
              + New
            </Button>
          </div>
        </div>
        {selectMode && (
          <div className="flex items-center gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              className="!text-xs !px-2 !py-0.5"
              onClick={selectAll}
            >
              All
            </Button>
            {selectedIds.size > 0 && (
              <>
                <span className="text-xs text-text-secondary bg-bg-hover px-1.5 py-0.5 rounded-full">
                  {selectedIds.size} selected
                </span>
                <Button
                  size="sm"
                  className="!bg-error hover:!bg-error/80 !text-xs !px-2 !py-0.5"
                  onClick={handleBatchDelete}
                >
                  Delete
                </Button>
              </>
            )}
            <Button
              variant="secondary"
              size="sm"
              className="!text-xs !px-2 !py-0.5"
              onClick={exitSelectMode}
            >
              Cancel
            </Button>
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
              className={`relative group rounded-md transition-colors ${
                selectMode ? 'cursor-pointer' : ''
              } ${
                isSelected
                  ? 'bg-accent/10 border-l-2 border-accent'
                  : isActive
                    ? 'bg-accent/10 border-l-2 border-accent'
                    : 'hover:bg-bg-hover border-l-2 border-transparent'
              }`}
            >
              {/* Checkbox (select mode only) */}
              {selectMode && (
                <div
                  className={`absolute top-2.5 left-1.5 w-4 h-4 rounded border flex items-center justify-center transition-colors z-10 ${
                    isSelected
                      ? 'border-accent bg-accent'
                      : 'border-border-subtle'
                  }`}
                >
                  {isSelected && (
                    <svg className="w-3 h-3 text-gray-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
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
                  <div className={`truncate ${isActive ? 'font-semibold text-text-primary' : 'text-text-primary'}`}>
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
                  className="absolute top-2 right-2 p-1 rounded-md text-text-tertiary hover:text-error hover:bg-error/10 opacity-0 group-hover:opacity-100 transition-all"
                  title="Delete session"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
