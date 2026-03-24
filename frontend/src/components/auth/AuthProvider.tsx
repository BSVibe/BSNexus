import { useEffect } from 'react'
import { useAuthStore } from '../../stores/authStore'

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const initialize = useAuthStore((s) => s.initialize)
  const isLoading = useAuthStore((s) => s.isLoading)

  useEffect(() => {
    initialize()
  }, [initialize])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    )
  }

  return <>{children}</>
}
