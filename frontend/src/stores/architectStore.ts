import { create } from 'zustand'
import type { DesignSession } from '../types/architect'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  isStreaming?: boolean
  createdAt: string
}

interface ArchitectState {
  sessionId: string | null
  projectId: string | null
  sessions: DesignSession[]
  messages: ChatMessage[]
  isStreaming: boolean
  isConnected: boolean

  setSessionId: (id: string | null) => void
  setProjectId: (id: string | null) => void
  setSessions: (sessions: DesignSession[]) => void
  setMessages: (messages: ChatMessage[]) => void
  addMessage: (message: ChatMessage) => void
  appendToLastMessage: (chunk: string) => void
  setStreaming: (streaming: boolean) => void
  setConnected: (connected: boolean) => void
  clearMessages: () => void
}

export const useArchitectStore = create<ArchitectState>((set) => ({
  sessionId: null,
  projectId: null,
  sessions: [],
  messages: [],
  isStreaming: false,
  isConnected: false,

  setSessionId: (id) => set({ sessionId: id }),
  setProjectId: (id) => set({ projectId: id }),
  setSessions: (sessions) => set({ sessions }),
  setMessages: (messages) => set({ messages }),
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),
  appendToLastMessage: (chunk) =>
    set((state) => {
      const messages = [...state.messages]
      const last = messages[messages.length - 1]
      if (last && last.role === 'assistant') {
        messages[messages.length - 1] = { ...last, content: last.content + chunk }
      }
      return { messages }
    }),
  setStreaming: (streaming) => set({ isStreaming: streaming }),
  setConnected: (connected) => set({ isConnected: connected }),
  clearMessages: () => set({ messages: [] }),
}))

export type { ChatMessage }
