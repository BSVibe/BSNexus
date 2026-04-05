import { useEffect, useRef } from "react"
import { useNavigate } from "react-router-dom"
import { useAuthStore } from "../stores/authStore"

export default function AuthCallbackPage() {
  const navigate = useNavigate()
  const handleCallback = useAuthStore((s) => s.handleCallback)
  const processed = useRef(false)

  useEffect(() => {
    if (processed.current) return
    processed.current = true

    // BSVibeAuth.handleCallback() handles hash parsing, state validation, and session storage
    handleCallback()
    navigate("/dashboard", { replace: true })
  }, [handleCallback, navigate])

  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-accent" />
    </div>
  )
}
