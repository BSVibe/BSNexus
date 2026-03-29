import type { Task, TaskStatus } from '../../types/task'
import { tasksApi } from '../../api/tasks'
import { useBoardStore } from '../../stores/boardStore'
import { Modal, Badge, Button } from '../common'
import { Clock, GitBranch, GitCommit, AlertTriangle, Layers } from 'lucide-react'

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
    <Modal open={true} onClose={onClose} title={task.title} footer={footer} width={560}>
      {/* Status badges */}
      <div className="mb-5 flex items-center gap-2 flex-wrap">
        <Badge color={task.status} label={task.status} />
        <Badge color={task.priority} label={task.priority} />
        <span className="ml-auto text-[11px] font-mono text-text-muted">v{task.version}</span>
      </div>

      {/* Description */}
      {task.description && (
        <div className="mb-5 rounded-lg bg-bg-elevated/50 border border-border/30 p-4">
          <p className="text-sm text-text-primary whitespace-pre-wrap leading-relaxed">{task.description}</p>
        </div>
      )}

      {/* Details grid */}
      <div className="mb-5 grid grid-cols-2 gap-4">
        <DetailItem icon={<Layers size={14} />} label="Phase" value={phaseLabel} />
        {task.branch_name && (
          <DetailItem icon={<GitBranch size={14} />} label="Branch" value={task.branch_name} mono />
        )}
        {showCommit && (
          <DetailItem icon={<GitCommit size={14} />} label="Commit" value={task.commit_hash!.slice(0, 8)} mono />
        )}
        <DetailItem icon={<Clock size={14} />} label="Created" value={new Date(task.created_at).toLocaleString()} />
        {task.started_at && (
          <DetailItem icon={<Clock size={14} />} label="Started" value={new Date(task.started_at).toLocaleString()} />
        )}
        {task.completed_at && (
          <DetailItem icon={<Clock size={14} />} label="Completed" value={new Date(task.completed_at).toLocaleString()} />
        )}
      </div>

      {/* Dependencies */}
      {task.depends_on.length > 0 && (
        <div className="mb-5">
          <h3 className="text-[11px] font-medium uppercase tracking-wider text-text-tertiary mb-2">
            Dependencies ({task.depends_on.length})
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {task.depends_on.map((depId) => (
              <span key={depId} className="rounded-md bg-bg-elevated px-2.5 py-1 text-xs text-text-secondary font-mono border border-border/30">
                {depId.slice(0, 8)}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Retry info */}
      {task.retry_count > 0 && (
        <div className="mb-5">
          <h3 className="text-[11px] font-medium uppercase tracking-wider text-text-tertiary mb-2">
            Retries ({task.retry_count}/{task.max_retries})
          </h3>
          {task.qa_feedback_history && task.qa_feedback_history.length > 0 && (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {task.qa_feedback_history.map((entry, idx) => (
                <div key={idx} className="rounded-lg bg-bg-elevated border border-border/30 p-3 text-xs">
                  <span className="font-semibold text-text-secondary">Attempt {String(entry.attempt)}:</span>{' '}
                  <span className="text-text-primary">{String(entry.feedback || entry.error || 'No details')}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Error message */}
      {task.error_message && (
        <div className="mb-5 rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle size={14} className="text-amber-500" />
            <h3 className="text-sm font-semibold text-amber-500">Error</h3>
          </div>
          <p className="text-sm text-text-primary whitespace-pre-wrap">{task.error_message}</p>
        </div>
      )}

      {/* QA Result */}
      {task.qa_result && (
        <div className="mb-5 rounded-lg border border-pink-500/20 bg-pink-500/5 p-4">
          <h3 className="text-sm font-semibold text-pink-500 mb-2">QA Result</h3>
          <pre className="text-xs text-text-primary whitespace-pre-wrap font-mono">{JSON.stringify(task.qa_result, null, 2)}</pre>
        </div>
      )}
    </Modal>
  )
}

function DetailItem({ icon, label, value, mono }: { icon: React.ReactNode; label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <span className="text-text-muted mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">
        <span className="text-[11px] text-text-tertiary block">{label}</span>
        <p className={`text-sm text-text-primary truncate ${mono ? 'font-mono' : ''}`}>{value}</p>
      </div>
    </div>
  )
}
