import { useEffect } from "react"
import { useNavigate } from "react-router-dom"

// Legacy callback page — will be removed in TASK-004.
// Shared cookie auth no longer uses redirect callbacks.
export default function AuthCallbackPage() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate("/dashboard", { replace: true })
  }, [navigate])

  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-accent" />
    </div>
  )
}
