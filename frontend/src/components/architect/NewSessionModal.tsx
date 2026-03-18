import { useState } from 'react'
import { Modal, Button } from '../common'

interface NewSessionModalProps {
  open: boolean
  onClose: () => void
  onCreateSession: () => Promise<void>
}

export default function NewSessionModal({ open, onClose, onCreateSession }: NewSessionModalProps) {
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleCreate = async () => {
    setCreating(true)
    setError(null)
    try {
      await onCreateSession()
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } }
      setError(axiosErr.response?.data?.detail || 'Failed to create session')
    } finally {
      setCreating(false)
    }
  }

  const footer = (
    <>
      <Button variant="secondary" onClick={onClose}>
        Cancel
      </Button>
      <Button onClick={handleCreate} disabled={creating} loading={creating}>
        Create Session
      </Button>
    </>
  )

  return (
    <Modal open={open} onClose={onClose} title="New Session" footer={footer} width={520}>
      <div className="space-y-3">
        {error && (
          <p className="text-sm text-red-500">{error}</p>
        )}
        <p className="text-sm text-text-secondary">
          Create a new architect session to design your project.
        </p>
      </div>
    </Modal>
  )
}
