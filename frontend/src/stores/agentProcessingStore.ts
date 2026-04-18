import { create } from 'zustand'

interface ProcessingInfo {
  agentName: string
  mode: string
}

interface AgentProcessingStore {
  processingAgents: Map<string, ProcessingInfo>
  setProcessing: (agentId: string, agentName: string, mode: string) => void
  clearProcessing: (agentId: string) => void
  clearAll: () => void
}

export const useAgentProcessingStore = create<AgentProcessingStore>((set) => ({
  processingAgents: new Map(),
  setProcessing: (agentId, agentName, mode) =>
    set((state) => {
      const next = new Map(state.processingAgents)
      next.set(agentId, { agentName, mode })
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
