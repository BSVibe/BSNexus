import type { Task } from '../../types/task'
import KanbanColumn from './KanbanColumn'

const columnOrder = ['waiting', 'ready', 'in_progress', 'review', 'done']
const columnLabels: Record<string, string> = {
  waiting: 'Waiting',
  ready: 'Ready',
  in_progress: 'In Progress',
  review: 'Review',
  done: 'Done',
}

interface Props {
  columns: Record<string, Task[]>
  onTaskClick?: (task: Task) => void
}

export default function KanbanBoard({ columns, onTaskClick }: Props) {
  return (
    <div className="flex gap-4 overflow-x-auto pb-4">
      {columnOrder.map((status) => (
        <KanbanColumn
          key={status}
          title={columnLabels[status]}
          status={status}
          tasks={columns[status] || []}
          onTaskClick={onTaskClick}
        />
      ))}
    </div>
  )
}
