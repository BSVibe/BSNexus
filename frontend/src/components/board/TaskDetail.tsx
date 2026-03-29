import type { Task, TaskStatus } from '../../types/task'
import { tasksApi } from '../../api/tasks'
import { useBoardStore } from '../../stores/boardStore'
import { Modal, Badge, Button } from '../common'
import { Clock, GitBranch, GitCommit, AlertTriangle, Layers, Link2, RefreshCw, Shield } from 'lucide-react'

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
    <div className="flex items-center gap-2">
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
    </div>
  ) : undefined

  return (
    <Modal open={true} onClose={onClose} title={task.title} footer={footer} width={600}>
      {/* Status & metadata row */}
      <div className="mb-6 flex items-center gap-2 flex-wrap">
        <Badge color={task.status} label={task.status} />
        <Badge color={task.priority} label={task.priority} />
        <Badge color="#6B7280" label={task.task_type} />
        <span className="ml-auto text-[11px] font-mono text-text-muted bg-bg-elevated px-2 py-0.5 rounded-md border border-border/30">
          v{task.version}
        </span>
      </div>

      {/* Description */}
      {task.description && (
        <div className="mb-6 rounded-xl bg-bg-elevated/60 border border-border/30 p-4">
          <p className="text-sm text-text-primary whitespace-pre-wrap leading-relaxed">{task.description}</p>
        </div>
      )}

      {/* Details grid */}
      <div className="mb-6 grid grid-cols-2 gap-4">
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
        <Section icon={<Link2 size={14} />} title={`Dependencies (${task.depends_on.length})`}>
          <div className="flex flex-wrap gap-2">
            {task.depends_on.map((depId) => (
              <span key={depId} className="rounded-lg bg-bg-elevated px-3 py-1.5 text-xs text-text-secondary font-mono border border-border/30">
                {depId.slice(0, 8)}
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* Retry info */}
      {task.retry_count > 0 && (
        <Section icon={<RefreshCw size={14} />} title={`Retries (${task.retry_count}/${task.max_retries})`}>
          {task.qa_feedback_history && task.qa_feedback_history.length > 0 && (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {task.qa_feedback_history.map((entry, idx) => (
                <div key={idx} className="rounded-lg bg-bg-primary border border-border/30 p-3 text-xs">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-semibold text-text-secondary">Attempt {String(entry.attempt)}</span>
                    {entry.type && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-bg-hover text-text-muted">
                        {String(entry.type)}
                      </span>
                    )}
                  </div>
                  <span className="text-text-primary">{String(entry.feedback || entry.error || 'No details')}</span>
                </div>
              ))}
            </div>
          )}
        </Section>
      )}

      {/* Error message */}
      {task.error_message && (
        <div className="mb-6 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle size={14} className="text-amber-500" />
            <h3 className="text-sm font-semibold text-amber-400">Error</h3>
          </div>
          <p className="text-sm text-text-primary whitespace-pre-wrap font-mono leading-relaxed">{task.error_message}</p>
        </div>
      )}

      {/* QA Result */}
      {task.qa_result && (
        <div className="mb-6 rounded-xl border border-pink-500/20 bg-pink-500/5 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Shield size={14} className="text-pink-500" />
            <h3 className="text-sm font-semibold text-pink-400">QA Result</h3>
          </div>
          <pre className="text-xs text-text-primary whitespace-pre-wrap font-mono bg-bg-primary rounded-lg p-3 border border-border/30 max-h-64 overflow-y-auto">
            {JSON.stringify(task.qa_result, null, 2)}
          </pre>
        </div>
      )}
    </Modal>
  )
}

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-text-tertiary">{icon}</span>
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-text-tertiary">
          {title}
        </h3>
      </div>
      {children}
    </div>
  )
}

function DetailItem({ icon, label, value, mono }: { icon: React.ReactNode; label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start gap-2.5 p-2.5 rounded-lg bg-bg-elevated/40 border border-border/20">
      <span className="text-text-tertiary mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">
        <span className="text-[11px] text-text-muted block">{label}</span>
        <p className={`text-sm text-text-primary truncate ${mono ? 'font-mono text-xs' : ''}`}>{value}</p>
      </div>
    </div>
  )
}
