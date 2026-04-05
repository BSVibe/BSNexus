import { create } from 'zustand'
import type { Agent, AgentOrgChartNode } from '../types/agent'
import { agentsApi } from '../api/agents'

interface AgentStore {
  agents: Agent[]
  orgChart: AgentOrgChartNode[]
  selectedAgent: Agent | null
  loading: boolean
  error: string | null

  fetchAgents: () => Promise<void>
  fetchOrgChart: () => Promise<void>
  selectAgent: (agent: Agent | null) => void
  createAgent: (data: Parameters<typeof agentsApi.create>[0]) => Promise<Agent>
  updateAgent: (id: string, data: Parameters<typeof agentsApi.update>[1]) => Promise<Agent>
  deleteAgent: (id: string) => Promise<void>
}

export const useAgentStore = create<AgentStore>((set) => ({
  agents: [],
  orgChart: [],
  selectedAgent: null,
  loading: false,
  error: null,

  fetchAgents: async () => {
    set({ loading: true, error: null })
    try {
      const agents = await agentsApi.list()
      set({ agents, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  fetchOrgChart: async () => {
    set({ loading: true, error: null })
    try {
      const orgChart = await agentsApi.getOrgChart()
      set({ orgChart, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  selectAgent: (agent) => set({ selectedAgent: agent }),

  createAgent: async (data) => {
    const agent = await agentsApi.create(data)
    set((s) => ({ agents: [...s.agents, agent] }))
    return agent
  },

  updateAgent: async (id, data) => {
    const updated = await agentsApi.update(id, data)
    set((s) => ({
      agents: s.agents.map((a) => (a.id === id ? updated : a)),
      selectedAgent: s.selectedAgent?.id === id ? updated : s.selectedAgent,
    }))
    return updated
  },

  deleteAgent: async (id) => {
    await agentsApi.delete(id)
    set((s) => ({
      agents: s.agents.filter((a) => a.id !== id),
      selectedAgent: s.selectedAgent?.id === id ? null : s.selectedAgent,
    }))
  },
}))
