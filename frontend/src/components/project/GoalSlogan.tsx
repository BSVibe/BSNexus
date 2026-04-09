import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { goalsApi } from '../../api/goals'

interface Props {
  projectId: string
}

export default function GoalSlogan({ projectId }: Props) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [popoverOpen, setPopoverOpen] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const popoverRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  const { data: goals = [] } = useQuery({
    queryKey: ['goals', projectId],
    queryFn: () => goalsApi.list({ project_id: projectId, level: 'project' }),
  })

  const goal = goals[0] ?? null

  const updateMutation = useMutation({
    mutationFn: ({ id, title, description }: { id: string; title: string; description: string }) =>
      goalsApi.update(id, { title, description }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals', projectId] })
      setEditing(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => goalsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals', projectId] })
      setEditing(false)
      setPopoverOpen(false)
    },
  })

  // Close popover on outside click
  useEffect(() => {
    if (!popoverOpen) return
    const handleClick = (e: MouseEvent) => {
      if (
        popoverRef.current && !popoverRef.current.contains(e.target as Node) &&
        triggerRef.current && !triggerRef.current.contains(e.target as Node)
      ) {
        setPopoverOpen(false)
        setEditing(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [popoverOpen])

  const handleOpen = () => {
    if (!goal) return
    setEditTitle(goal.title)
    setEditDesc(goal.description || '')
    setPopoverOpen(true)
  }

  const handleSave = () => {
    if (!goal) return
    // Empty title → delete the goal
    if (!editTitle.trim()) {
      deleteMutation.mutate(goal.id)
      return
    }
    updateMutation.mutate({ id: goal.id, title: editTitle.trim(), description: editDesc.trim() })
  }

  if (!goal) return null

  return (
    <span className="relative inline-flex items-center">
      <button
        ref={triggerRef}
        onClick={handleOpen}
        className="text-sm text-text-tertiary hover:text-text-secondary transition-colors truncate max-w-[300px]"
        title={goal.title}
      >
        &mdash; {goal.title}
      </button>

      {popoverOpen && (
        <div
          ref={popoverRef}
          className="absolute top-full left-0 mt-2 w-80 bg-stitch-surface-container border border-stitch-outline-variant/20 rounded-xl shadow-2xl z-50 p-4"
        >
          {editing ? (
            <div className="space-y-3">
              <input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="w-full px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/20 rounded-lg text-sm text-text-primary focus:outline-none focus:border-stitch-primary"
                placeholder="Goal title"
                autoFocus
              />
              <textarea
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
                className="w-full px-3 py-2 bg-stitch-surface border border-stitch-outline-variant/20 rounded-lg text-sm text-text-primary focus:outline-none focus:border-stitch-primary resize-none"
                placeholder="Description (optional)"
                rows={3}
              />
              <div className="flex items-center justify-between gap-2">
                <button
                  onClick={() => goal && deleteMutation.mutate(goal.id)}
                  disabled={deleteMutation.isPending}
                  className="px-3 py-1.5 text-xs text-stitch-error hover:bg-stitch-error/10 rounded-lg transition-colors disabled:opacity-40"
                >
                  {deleteMutation.isPending ? '...' : 'Clear goal'}
                </button>
                <div className="flex gap-2">
                  <button
                    onClick={() => setEditing(false)}
                    className="px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleSave}
                    disabled={updateMutation.isPending || deleteMutation.isPending}
                    className="px-3 py-1.5 text-xs bg-stitch-primary text-white rounded-lg hover:bg-stitch-primary/80 disabled:opacity-40"
                  >
                    {updateMutation.isPending || deleteMutation.isPending ? '...' : 'Save'}
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div>
              <h4 className="text-sm font-bold text-text-primary mb-1">{goal.title}</h4>
              {goal.description && (
                <p className="text-xs text-text-secondary mb-3">{goal.description}</p>
              )}
              <button
                onClick={() => setEditing(true)}
                className="text-[10px] text-stitch-primary hover:underline"
              >
                Edit goal
              </button>
            </div>
          )}
        </div>
      )}
    </span>
  )
}
