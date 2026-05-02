import { createContext, useContext } from 'react'

interface AuthContextValue {
  user: {
    id: string
    email: string
    tenantId: string
    tenantName: string | null
    role: string
  } | null
  loading: boolean
  login: () => void
  logout: () => Promise<void>
  tenants: Array<{ id: string; name: string; role?: string }>
  switchTenant: (id: string) => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuthContext() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuthContext must be used within AuthProvider')
  return ctx
}
