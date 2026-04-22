import { create } from 'zustand'

export type PlanNodeType = 'phase' | 'task'

export interface PlanSelection {
  type: PlanNodeType
  id: string
}

interface PlanStore {
  /** Currently selected node in the Plan tree (drives the right detail panel). */
  selectedNode: PlanSelection | null
  /** Phases that are expanded in the tree (collapsed by default). */
  expandedPhases: Set<string>
  /** Optional agent filter — when set, the tree highlights tasks for this agent. */
  highlightedAgentId: string | null

  selectNode: (selection: PlanSelection | null) => void
  togglePhase: (phaseId: string) => void
  expandPhase: (phaseId: string) => void
  collapsePhase: (phaseId: string) => void
  setHighlightedAgent: (agentId: string | null) => void
  reset: () => void
}

export const usePlanStore = create<PlanStore>((set) => ({
  selectedNode: null,
  expandedPhases: new Set<string>(),
  highlightedAgentId: null,

  selectNode: (selection) => set({ selectedNode: selection }),

  togglePhase: (phaseId) =>
    set((state) => {
      const next = new Set(state.expandedPhases)
      if (next.has(phaseId)) next.delete(phaseId)
      else next.add(phaseId)
      return { expandedPhases: next }
    }),

  expandPhase: (phaseId) =>
    set((state) => {
      if (state.expandedPhases.has(phaseId)) return state
      const next = new Set(state.expandedPhases)
      next.add(phaseId)
      return { expandedPhases: next }
    }),

  collapsePhase: (phaseId) =>
    set((state) => {
      if (!state.expandedPhases.has(phaseId)) return state
      const next = new Set(state.expandedPhases)
      next.delete(phaseId)
      return { expandedPhases: next }
    }),

  setHighlightedAgent: (agentId) =>
    set((state) => ({
      highlightedAgentId: state.highlightedAgentId === agentId ? null : agentId,
    })),

  reset: () =>
    set({
      selectedNode: null,
      expandedPhases: new Set<string>(),
      highlightedAgentId: null,
    }),
}))
