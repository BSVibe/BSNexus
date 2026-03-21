import { create } from 'zustand'
import type { Task, TaskStatus, BoardResponse, PhaseInfo } from '../types/task'

interface BoardStats {
  total: number
  done: number
  completionRate: number
}

interface BoardState {
  columns: Record<string, Task[]>
  stats: Record<string, number>
  phases: Record<string, PhaseInfo>
  redesignTasks: Task[]
  manualRedesignTaskIds: Set<string>
  selectedTask: Task | null
  isConnected: boolean

  setBoard: (data: BoardResponse) => void
  setSelectedTask: (task: Task | null) => void
  setConnected: (connected: boolean) => void
  moveTask: (taskId: string, from: string, to: string) => void
  updateTask: (task: Task) => void
  addManualRedesignTaskId: (taskId: string) => void
  clearManualRedesignTaskIds: () => void
  getBoardStats: () => BoardStats
}

export const useBoardStore = create<BoardState>((set, get) => ({
  columns: {},
  stats: {},
  phases: {},
  redesignTasks: [],
  manualRedesignTaskIds: new Set<string>(),
  selectedTask: null,
  isConnected: false,

  setBoard: (data) =>
    set((state) => {
      const redesignTasks = data.redesign_tasks || []
      const redesignTaskIds = new Set(redesignTasks.map((t) => t.id))
      // Prune manual IDs that are no longer in redesign (resolved or moved out)
      const pruned = new Set<string>()
      for (const id of state.manualRedesignTaskIds) {
        if (redesignTaskIds.has(id)) {
          pruned.add(id)
        }
      }
      return {
        columns: Object.fromEntries(
          Object.entries(data.columns).map(([key, col]) => [key, col.tasks])
        ),
        stats: data.stats,
        phases: data.phases || {},
        redesignTasks,
        manualRedesignTaskIds: pruned,
      }
    }),

  setSelectedTask: (task) => set({ selectedTask: task }),
  setConnected: (connected) => set({ isConnected: connected }),

  moveTask: (taskId, from, to) =>
    set((state) => {
      const columns = { ...state.columns }
      let redesignTasks = state.redesignTasks
      let task: Task | undefined

      // Source: redesignTasks or columns
      if (from === 'redesign') {
        const idx = redesignTasks.findIndex((t) => t.id === taskId)
        if (idx === -1) return state
        redesignTasks = [...redesignTasks]
        task = redesignTasks.splice(idx, 1)[0]
      } else {
        const fromTasks = [...(columns[from] || [])]
        const idx = fromTasks.findIndex((t) => t.id === taskId)
        if (idx === -1) return state
        task = fromTasks.splice(idx, 1)[0]
        columns[from] = fromTasks
      }

      const movedTask = { ...task, status: to as TaskStatus }

      // Destination: redesignTasks or columns
      if (to === 'redesign') {
        redesignTasks = [...redesignTasks, movedTask]
      } else {
        columns[to] = [...(columns[to] || []), movedTask]
      }

      return { columns, redesignTasks }
    }),

  updateTask: (updated) =>
    set((state) => {
      const columns = { ...state.columns }
      for (const key of Object.keys(columns)) {
        columns[key] = columns[key].map((t) => (t.id === updated.id ? updated : t))
      }
      const redesignTasks = state.redesignTasks.map((t) => (t.id === updated.id ? updated : t))
      return { columns, redesignTasks, selectedTask: state.selectedTask?.id === updated.id ? updated : state.selectedTask }
    }),

  addManualRedesignTaskId: (taskId) =>
    set((state) => {
      const updated = new Set(state.manualRedesignTaskIds)
      updated.add(taskId)
      return { manualRedesignTaskIds: updated }
    }),

  clearManualRedesignTaskIds: () => set({ manualRedesignTaskIds: new Set<string>() }),

  getBoardStats: () => {
    const { columns } = get()
    const allTasks = Object.values(columns).flat()
    const total = allTasks.length
    const done = (columns['done'] || []).length
    return { total, done, completionRate: total > 0 ? (done / total) * 100 : 0 }
  },
}))
