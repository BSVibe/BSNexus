import { useState, useEffect, useMemo } from 'react'
import type { Task } from '../../types/task'
import { architectApi } from '../../api/architect'
import { useBoardStore } from '../../stores/boardStore'
import { Badge, Button } from '../common'

interface Props {
  tasks: Task[]
  onDone: () => void
}

export default function RedesignView({ tasks, onDone }: Props) {
  const { manualRedesignTaskIds } = useBoardStore()

  // Split tasks: manual = flagged by auto_redesign_failed event, auto = the rest
  const manualTasks = useMemo(
    () => tasks.filter((t) => manualRedesignTaskIds.has(t.id)),
    [tasks, manualRedesignTaskIds],
  )
  const autoTasks = useMemo(
    () => tasks.filter((t) => !manualRedesignTaskIds.has(t.id)),
    [tasks, manualRedesignTaskIds],
  )

  // If no redesign tasks remain, auto-return to board
  useEffect(() => {
    if (tasks.length === 0) {
      onDone()
    }
  }, [tasks.length, onDone])

  // If all tasks are auto-processing (no manual ones), show loading screen
  if (manualTasks.length === 0) {
    return <AutoRedesignLoading tasks={autoTasks} />
  }

  // If there are manual tasks, show manual intervention UI
  return <ManualRedesignView tasks={manualTasks} autoTasks={autoTasks} onDone={onDone} />
}

// ── Auto-Redesign Loading Screen ──────────────────────────────────

function AutoRedesignLoading({ tasks }: { tasks: Task[] }) {
  return (
    <div className="flex flex-col items-center justify-center h-[calc(100vh-8rem)] bg-bg-primary">
      {/* Spinner */}
      <div className="mb-8">
        <div className="w-16 h-16 border-4 border-border rounded-full animate-spin" style={{ borderTopColor: 'var(--accent)' }} />
      </div>

      {/* Message */}
      <h2 className="text-xl font-semibold text-text-primary mb-2">
        Architect is redesigning the phase...
      </h2>
      <p className="text-sm text-text-secondary mb-8">
        Analyzing failure patterns and redesigning incomplete tasks.
      </p>

      {/* Task list (compact) */}
      <div className="w-full max-w-md space-y-2">
        {tasks.map((task) => (
          <div
            key={task.id}
            className="flex items-center gap-3 rounded-lg bg-bg-elevated p-3"
          >
            <div className="w-4 h-4 border-2 border-border rounded-full animate-spin flex-shrink-0" style={{ borderTopColor: 'var(--accent)' }} />
            <span className="text-sm text-text-primary truncate">{task.title}</span>
            <Badge color="redesign" label={`${task.retry_count}/${task.max_retries}`} size="sm" />
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Manual Redesign View (fallback when auto-redesign limit exceeded) ──

function ManualRedesignView({
  tasks,
  autoTasks,
  onDone,
}: {
  tasks: Task[]
  autoTasks: Task[]
  onDone: () => void
}) {
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [result, setResult] = useState<{ reasoning: string; tasks_kept: number; tasks_deleted: number; tasks_created: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  // All redesign tasks should belong to the same phase
  const phaseId = tasks[0]?.phase_id || ''

  const handleRedesignPhase = async () => {
    if (!phaseId) return
    setIsSubmitting(true)
    setError(null)
    try {
      const response = await architectApi.redesignPhase(phaseId)
      setResult(response)
      // Give the board time to refetch before returning
      setTimeout(onDone, 2000)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Redesign failed'
      setError(msg)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] bg-bg-primary">
      {/* Left panel: task list */}
      <div className="w-80 border-r border-border overflow-y-auto">
        <div className="p-4 border-b border-border">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-text-primary">Phase Redesign</h2>
            <span className="text-xs text-text-tertiary">{tasks.length} failed task(s)</span>
          </div>
          <p className="text-xs text-text-secondary mt-1">
            Auto-redesign exhausted. The entire phase will be redesigned by the Architect.
          </p>
        </div>

        {/* Auto-processing tasks (if any still pending) */}
        {autoTasks.length > 0 && (
          <div className="p-4 border-b border-border">
            <p className="text-xs text-text-secondary mb-2">
              Auto-redesigning {autoTasks.length} other task(s)...
            </p>
            {autoTasks.map((task) => (
              <div key={task.id} className="flex items-center gap-2 py-1">
                <div className="w-3 h-3 border-2 border-border rounded-full animate-spin flex-shrink-0" style={{ borderTopColor: 'var(--accent)' }} />
                <span className="text-xs text-text-tertiary truncate">{task.title}</span>
              </div>
            ))}
          </div>
        )}

        {/* Failed tasks */}
        <div className="p-2 space-y-1">
          {tasks.map((task) => (
            <div
              key={task.id}
              className="w-full text-left rounded-lg p-3 bg-bg-elevated border border-border"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-text-primary truncate">{task.title}</span>
                <Badge color="redesign" label={`${task.retry_count}/${task.max_retries}`} size="sm" />
              </div>
              {task.error_message && (
                <p className="text-xs text-text-tertiary mt-1 line-clamp-2">
                  {task.error_message}
                </p>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Right panel: action */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-semibold text-text-primary">Phase Redesign Required</h2>
            <Button variant="ghost" size="sm" onClick={onDone}>
              Back to Board
            </Button>
          </div>

          <p className="text-sm text-text-secondary mb-6">
            The Architect will analyze all incomplete tasks in this phase and redesign them to resolve the failures.
            Tasks may be modified, removed, or new tasks may be added.
          </p>

          {/* Task failure details */}
          {tasks.map((task) => (
            <div
              key={task.id}
              className="mb-4 rounded-lg border p-4"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--status-redesign) 10%, transparent)',
                borderColor: 'var(--status-redesign)',
              }}
            >
              <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--status-redesign)' }}>
                {task.title}
              </h3>

              {/* Redesign reason / error message */}
              {task.error_message && (
                <div className="mb-3">
                  <span className="text-xs font-medium text-text-secondary">Redesign Reason</span>
                  <p className="text-sm text-text-primary whitespace-pre-wrap mt-0.5">{task.error_message}</p>
                </div>
              )}

              {/* QA feedback history */}
              {task.qa_feedback_history && task.qa_feedback_history.length > 0 && (
                <div>
                  <span className="text-xs font-medium text-text-secondary">
                    QA History ({task.qa_feedback_history.length} attempt{task.qa_feedback_history.length > 1 ? 's' : ''})
                  </span>
                  <div className="mt-1 space-y-1.5 max-h-48 overflow-y-auto">
                    {task.qa_feedback_history.map((entry, idx) => (
                      <div key={idx} className="rounded bg-bg-primary/50 p-2 text-xs">
                        <span className="font-medium text-text-secondary">Attempt {entry.attempt ?? '?'}:</span>{' '}
                        <span className="text-text-primary">{entry.feedback || entry.error || 'No details'}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}

          {/* Result */}
          {result && (
            <div className="mb-6 rounded-lg border border-success bg-success/10 p-4">
              <h3 className="text-sm font-medium text-success mb-2">Redesign Complete</h3>
              <p className="text-sm text-text-primary mb-2">{result.reasoning}</p>
              <div className="flex gap-4 text-xs text-text-secondary">
                <span>Kept: {result.tasks_kept}</span>
                <span>Deleted: {result.tasks_deleted}</span>
                <span>Created: {result.tasks_created}</span>
              </div>
            </div>
          )}

          {/* Error message */}
          {error && (
            <div className="mb-6 rounded-lg border border-error bg-error/10 p-4">
              <p className="text-sm text-error">{error}</p>
            </div>
          )}

          {/* Action button */}
          {!result && (
            <Button
              variant="primary"
              size="lg"
              onClick={handleRedesignPhase}
              disabled={isSubmitting}
            >
              {isSubmitting ? 'Redesigning Phase...' : 'Redesign Phase with Architect'}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
