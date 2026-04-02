import type { Task, TaskStatus } from '../../types/task'
import { tasksApi } from '../../api/tasks'
import { useBoardStore } from '../../stores/boardStore'
import { Modal, Button } from '../common'

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
  const phaseLabel = phaseInfo ? `Phase ${phaseInfo.order} - ${phaseInfo.name}` : `Phase ${task.phase_id.slice(0, 8)}`
  const showCommit = ['review', 'done'].includes(task.status) && task.commit_hash

  const footer = transitions.length > 0 ? (
    <div className="flex items-center gap-2">
      {transitions.map((t) => (
        <Button
          key={t.to}
          onClick={() => handleTransition(t.to)}
          variant="primary"
          size="md"
        >
          {t.label}
        </Button>
      ))}
    </div>
  ) : undefined

  return (
    <Modal open={true} onClose={onClose} title={task.title} footer={footer} width={900}>
      <div className="flex flex-col lg:flex-row gap-8">
        {/* Left panel (2/3) */}
        <div className="lg:w-2/3 space-y-6">
          {/* Status & action buttons */}
          <div>
            <div className="flex items-center gap-4 mb-2">
              <span className="px-3 py-1 bg-stitch-secondary-container text-stitch-on-secondary-container rounded-full text-xs font-bold uppercase tracking-wider">
                {task.status.replace('_', ' ')}
              </span>
              <span className="text-text-secondary text-sm font-medium">Task ID: {task.id.slice(0, 12)}</span>
            </div>
          </div>

          {/* Description */}
          {task.description && (
            <section className="bg-stitch-surface-low p-6 rounded-lg">
              <h2 className="text-xs font-bold uppercase tracking-[0.15em] text-text-secondary mb-4">Description</h2>
              <div className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
                {task.description}
              </div>
            </section>
          )}

          {/* Error message */}
          {task.error_message && (
            <section className="bg-stitch-surface-low rounded-lg overflow-hidden">
              <div className="px-6 py-4 flex justify-between items-center border-b border-stitch-outline-variant/10">
                <h2 className="text-xs font-bold uppercase tracking-[0.15em] text-text-secondary">Error Log</h2>
                <span className="text-[10px] font-mono text-stitch-error">ERROR</span>
              </div>
              <div className="p-6 bg-stitch-surface-lowest font-mono text-xs space-y-2 max-h-64 overflow-y-auto">
                <div className="flex gap-4">
                  <span className="text-text-muted">[ERROR]</span>
                  <span className="text-stitch-error">{task.error_message}</span>
                </div>
              </div>
            </section>
          )}

          {/* QA Result */}
          {task.qa_result && (
            <section className="bg-stitch-surface-low rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-stitch-outline-variant/10">
                <h2 className="text-xs font-bold uppercase tracking-[0.15em] text-text-secondary">QA Result</h2>
              </div>
              <div className="p-4 bg-stitch-surface-lowest font-mono text-[13px] leading-6 overflow-x-auto max-h-64 overflow-y-auto">
                <pre className="text-text-primary whitespace-pre-wrap">
                  {JSON.stringify(task.qa_result, null, 2)}
                </pre>
              </div>
            </section>
          )}

          {/* Retry history */}
          {task.retry_count > 0 && task.qa_feedback_history && task.qa_feedback_history.length > 0 && (
            <section className="bg-stitch-surface-low rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-stitch-outline-variant/10">
                <h2 className="text-xs font-bold uppercase tracking-[0.15em] text-text-secondary">
                  Retry History ({task.retry_count}/{task.max_retries})
                </h2>
              </div>
              <div className="p-4 space-y-2 max-h-48 overflow-y-auto">
                {task.qa_feedback_history.map((entry, idx) => (
                  <div key={idx} className="bg-stitch-surface p-3 rounded-lg border-l-2 border-stitch-outline-variant/30">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[10px] font-bold text-stitch-primary">Attempt {String(entry.attempt)}</span>
                      {entry.type && (
                        <span className="text-[10px] font-medium text-text-tertiary">{String(entry.type)}</span>
                      )}
                    </div>
                    <p className="text-xs leading-relaxed text-text-primary">{String(entry.feedback || entry.error || 'No details')}</p>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        {/* Right panel (1/3) */}
        <div className="lg:w-1/3 space-y-6">
          {/* Task Properties */}
          <section className="bg-stitch-surface-container p-6 rounded-lg shadow-xl">
            <h2 className="text-xs font-bold uppercase tracking-[0.15em] text-text-secondary mb-6">Task Properties</h2>
            <div className="space-y-6">
              <PropertyRow label="Phase" value={phaseLabel} />
              <PropertyRow label="Type">
                <span className="px-2 py-0.5 bg-stitch-primary-container/20 text-stitch-primary border border-stitch-primary/20 rounded text-[11px] font-bold">
                  {task.task_type}
                </span>
              </PropertyRow>
              <PropertyRow label="Priority">
                <div className="flex items-center gap-1.5 text-stitch-error">
                  <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>priority_high</span>
                  <span className="text-sm font-bold">{task.priority}</span>
                </div>
              </PropertyRow>
              <PropertyRow label="Version">
                <span className="font-mono text-xs">v{task.version}</span>
              </PropertyRow>
              {task.branch_name && (
                <PropertyRow label="Branch">
                  <span className="font-mono text-xs">{task.branch_name}</span>
                </PropertyRow>
              )}
              {showCommit && (
                <PropertyRow label="Commit">
                  <span className="font-mono text-xs">{task.commit_hash!.slice(0, 8)}</span>
                </PropertyRow>
              )}

              <div className="pt-4 border-t border-stitch-outline-variant/10 space-y-2">
                <div className="flex justify-between text-[11px]">
                  <span className="text-text-secondary">Created</span>
                  <span className="text-text-primary">{new Date(task.created_at).toLocaleString()}</span>
                </div>
                {task.started_at && (
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-secondary">Started</span>
                    <span className="text-text-primary">{new Date(task.started_at).toLocaleString()}</span>
                  </div>
                )}
                {task.completed_at && (
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-secondary">Completed</span>
                    <span className="text-text-primary">{new Date(task.completed_at).toLocaleString()}</span>
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* Dependencies */}
          {task.depends_on.length > 0 && (
            <section className="space-y-4">
              <h2 className="text-xs font-bold uppercase tracking-[0.15em] text-text-secondary">
                Dependencies ({task.depends_on.length})
              </h2>
              <div className="space-y-2">
                {task.depends_on.map((depId) => (
                  <div key={depId} className="p-3 bg-stitch-surface-low rounded-lg hover:bg-stitch-surface-container transition-colors cursor-pointer group">
                    <p className="text-xs font-semibold text-white group-hover:text-stitch-primary transition-colors font-mono">
                      {depId.slice(0, 12)}
                    </p>
                    <p className="text-[10px] text-text-secondary mt-1">Dependency</p>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </Modal>
  )
}

function PropertyRow({ label, value, children }: { label: string; value?: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-text-secondary">{label}</span>
      {value ? <span className="text-sm font-semibold text-white">{value}</span> : children}
    </div>
  )
}
