import { useAuth } from '../../hooks/useAuth'
import { AuthContext } from './AuthContext'

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const auth = useAuth()

  if (auth.loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    )
  }

  return (
    <AuthContext.Provider value={auth}>
      {children}
    </AuthContext.Provider>
  )
}
