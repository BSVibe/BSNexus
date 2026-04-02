import type { Task } from '../../types/task'
import TaskCard from './TaskCard'

interface Props {
  title: string
  status: string
  tasks: Task[]
  onTaskClick?: (task: Task) => void
}

export default function KanbanColumn({ title, status, tasks, onTaskClick }: Props) {
  const isDone = status === 'done'

  return (
    <div className={`w-80 flex flex-col ${isDone ? 'opacity-60' : ''}`}>
      <div className="flex items-center justify-between mb-4 px-1">
        <h3 className="text-xs font-bold uppercase tracking-[0.05em] text-text-secondary">
          {title} <span className="ml-2 text-[10px] opacity-50">{tasks.length}</span>
        </h3>
        {status === 'waiting' && (
          <button className="text-text-secondary hover:text-white">
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>add</span>
          </button>
        )}
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto pr-2">
        {tasks.map((task) => (
          <TaskCard key={task.id} task={task} onClick={() => onTaskClick?.(task)} isDone={isDone} />
        ))}
        {tasks.length === 0 && (
          <div className="flex flex-col items-center justify-center py-10 text-text-muted">
            <span className="material-symbols-outlined mb-2 opacity-40" style={{ fontSize: '24px' }}>inbox</span>
            <span className="text-xs">No tasks</span>
          </div>
        )}
      </div>
    </div>
  )
}
