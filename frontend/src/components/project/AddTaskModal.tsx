import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Modal } from '../common'
import { tasksApi } from '../../api/tasks'
import { agentsApi } from '../../api/agents'
import type { Project } from '../../types/project'

const INPUT_CLASS =
  'w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary'

const TASK_TYPES = ['feature', 'bug', 'improvement', 'test', 'chore', 'refactor'] as const
const PRIORITIES = ['low', 'medium', 'high', 'critical'] as const

interface AddTaskModalProps {
  open: boolean
  onClose: () => void
  project: Project
}

export default function AddTaskModal({ open, onClose, project }: AddTaskModalProps) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [taskType, setTaskType] = useState<string>('feature')
  const [priority, setPriority] = useState<string>('medium')
  const [phaseId, setPhaseId] = useState(project.phases[0]?.id || '')
  const [agentId, setAgentId] = useState<string>('')

  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsApi.list(),
  })

  const createMutation = useMutation({
    mutationFn: () => tasksApi.create({
      project_id: project.id,
      phase_id: phaseId,
      title,
      description,
      task_type: taskType as 'feature',
      priority: priority as 'medium',
      creator_agent_id: agentId || undefined,
      source: 'manual',
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['board', project.id] })
      onClose()
      setTitle('')
      setDescription('')
      setTaskType('feature')
      setPriority('medium')
      setAgentId('')
    },
  })

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add Task"
      width={480}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            size="sm"
            loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}
            disabled={!title.trim() || !phaseId}
          >
            Create Task
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="block text-sm text-text-secondary mb-1.5">Title *</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Implement login API"
            className={INPUT_CLASS}
            autoFocus
          />
        </div>

        <div>
          <label className="block text-sm text-text-secondary mb-1.5">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Describe what needs to be done..."
            rows={3}
            className={INPUT_CLASS + ' resize-none'}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-text-tertiary mb-1">Type</label>
            <select value={taskType} onChange={(e) => setTaskType(e.target.value)} className={INPUT_CLASS}>
              {TASK_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-text-tertiary mb-1">Priority</label>
            <select value={priority} onChange={(e) => setPriority(e.target.value)} className={INPUT_CLASS}>
              {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        </div>

        <div>
          <label className="block text-xs text-text-tertiary mb-1">Phase</label>
          <select value={phaseId} onChange={(e) => setPhaseId(e.target.value)} className={INPUT_CLASS}>
            {project.phases.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            {project.phases.length === 0 && <option value="">No phases</option>}
          </select>
        </div>

        <div>
          <label className="block text-xs text-text-tertiary mb-1">Assign to Agent</label>
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)} className={INPUT_CLASS}>
            <option value="">Unassigned</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.role})</option>)}
          </select>
        </div>

        {createMutation.isError && (
          <p className="text-xs text-stitch-error">{(createMutation.error as Error).message}</p>
        )}
      </div>
    </Modal>
  )
}
