import { useAuth } from '../../hooks/useAuth'
import { AuthContext } from './AuthContext'
import { usePathname } from 'next/navigation'

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const auth = useAuth({ probeRemoteSession: pathname !== '/' })

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
