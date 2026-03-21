import type { Task, TaskStatus } from '../../types/task'
import { tasksApi } from '../../api/tasks'
import { useBoardStore } from '../../stores/boardStore'
import { Modal, Badge, Button } from '../common'

const allowedTransitions: Partial<Record<TaskStatus, { label: string; to: TaskStatus }[]>> = {}

interface Props {
  task: Task
  onClose: () => void
}

export default function TaskDetail({ task, onClose }: Props) {
  const { moveTask, updateTask, phases } = useBoardStore()
  const transitions = allowedTransitions[task.status] || []

  const handleTransition = async (to: TaskStatus) => {
    try {
      const result = await tasksApi.transition(task.id, {
        new_status: to,
        actor: 'user',
        expected_version: task.version,
      })
      moveTask(task.id, task.status, to)
      updateTask({ ...task, status: to, version: result.transition ? task.version + 1 : task.version })
    } catch {
      // Error handling
    }
  }

  const phaseInfo = phases[task.phase_id]
  const phaseLabel = phaseInfo ? `Phase ${phaseInfo.order} — ${phaseInfo.name}` : `Phase ${task.phase_id.slice(0, 8)}`
  const showCommit = ['review', 'done'].includes(task.status) && task.commit_hash

  const footer = transitions.length > 0 ? (
    <>
      {transitions.map((t) => (
        <Button
          key={t.to}
          onClick={() => handleTransition(t.to)}
          variant="primary"
          size="md"
        >
          {t.label} &rarr; {t.to}
        </Button>
      ))}
    </>
  ) : undefined

  return (
    <Modal open={true} onClose={onClose} title={task.title} footer={footer} width={520}>
      {/* Status and priority */}
      <div className="mb-4 flex items-center gap-2">
        <Badge color={task.status} label={task.status} />
        <Badge color={task.priority} label={task.priority} />
        <span className="text-xs text-text-tertiary">v{task.version}</span>
      </div>

      {/* Description */}
      {task.description && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-text-secondary mb-1">Description</h3>
          <p className="text-sm text-text-primary whitespace-pre-wrap">{task.description}</p>
        </div>
      )}

      {/* Details grid */}
      <div className="mb-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <span className="text-text-secondary">Phase</span>
          <p className="text-text-primary truncate">{phaseLabel}</p>
        </div>
        {task.branch_name && (
          <div>
            <span className="text-text-secondary">Branch</span>
            <p className="text-text-primary truncate">{task.branch_name}</p>
          </div>
        )}
        {showCommit && (
          <div>
            <span className="text-text-secondary">Commit</span>
            <p className="text-text-primary font-mono">{task.commit_hash!.slice(0, 8)}</p>
          </div>
        )}
        <div>
          <span className="text-text-secondary">Created</span>
          <p className="text-text-primary">{new Date(task.created_at).toLocaleString()}</p>
        </div>
        {task.started_at && (
          <div>
            <span className="text-text-secondary">Started</span>
            <p className="text-text-primary">{new Date(task.started_at).toLocaleString()}</p>
          </div>
        )}
        {task.completed_at && (
          <div>
            <span className="text-text-secondary">Completed</span>
            <p className="text-text-primary">{new Date(task.completed_at).toLocaleString()}</p>
          </div>
        )}
      </div>

      {/* Dependencies */}
      {task.depends_on.length > 0 && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-text-secondary mb-1">Dependencies ({task.depends_on.length})</h3>
          <div className="flex flex-wrap gap-1">
            {task.depends_on.map((depId) => (
              <span key={depId} className="rounded bg-bg-elevated px-2 py-0.5 text-xs text-text-secondary font-mono">
                {depId.slice(0, 8)}...
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Retry info */}
      {task.retry_count > 0 && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-text-secondary mb-1">
            Retries ({task.retry_count}/{task.max_retries})
          </h3>
          {task.qa_feedback_history && task.qa_feedback_history.length > 0 && (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {task.qa_feedback_history.map((entry, idx) => (
                <div key={idx} className="rounded bg-bg-elevated p-2 text-xs">
                  <span className="font-medium">Attempt {String(entry.attempt)}:</span>{' '}
                  {String(entry.feedback || entry.error || 'No details')}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Error message */}
      {task.error_message && (
        <div
          className="mb-4 rounded-md border p-3"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--status-redesign) 10%, transparent)',
            borderColor: 'var(--status-redesign)',
          }}
        >
          <h3
            className="text-sm font-medium mb-1"
            style={{ color: 'var(--status-redesign)' }}
          >
            Error
          </h3>
          <p className="text-sm text-text-primary whitespace-pre-wrap">{task.error_message}</p>
        </div>
      )}

      {/* QA Result */}
      {task.qa_result && (
        <div
          className="mb-4 rounded-md border p-3"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--status-review) 10%, transparent)',
            borderColor: 'var(--status-review)',
          }}
        >
          <h3
            className="text-sm font-medium mb-1"
            style={{ color: 'var(--status-review)' }}
          >
            QA Result
          </h3>
          <pre className="text-xs text-text-primary whitespace-pre-wrap">{JSON.stringify(task.qa_result, null, 2)}</pre>
        </div>
      )}
    </Modal>
  )
}
