import { create } from 'zustand'

interface ProcessingInfo {
  agentName: string
  mode: string
  activity: string
}

interface AgentProcessingStore {
  processingAgents: Map<string, ProcessingInfo>
  setProcessing: (agentId: string, agentName: string, mode: string, activity?: string) => void
  updateActivity: (agentId: string, activity: string) => void
  clearProcessing: (agentId: string) => void
  clearAll: () => void
}

export const useAgentProcessingStore = create<AgentProcessingStore>((set) => ({
  processingAgents: new Map(),
  setProcessing: (agentId, agentName, mode, activity = '') =>
    set((state) => {
      const next = new Map(state.processingAgents)
      next.set(agentId, { agentName, mode, activity })
      return { processingAgents: next }
    }),
  updateActivity: (agentId, activity) =>
    set((state) => {
      const existing = state.processingAgents.get(agentId)
      if (!existing) return state
      const next = new Map(state.processingAgents)
      next.set(agentId, { ...existing, activity })
      return { processingAgents: next }
    }),
  clearProcessing: (agentId) =>
    set((state) => {
      const next = new Map(state.processingAgents)
      next.delete(agentId)
      return { processingAgents: next }
    }),
  clearAll: () => set({ processingAgents: new Map() }),
}))
