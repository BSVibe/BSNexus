import { useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { architectApi } from '../api/architect'
import { parseSSEStream } from '../utils/sse'
import { Button, Modal } from '../components/common'
import Header from '../components/layout/Header'
import { useAuthStore } from '../stores/authStore'
import { useToastStore } from '../stores/toastStore'

type MigratePhase = 'idle' | 'analyze' | 'llm' | 'save' | 'done' | 'error'

// Expected LLM output size in chars — used for progress bar estimation
const ESTIMATED_LLM_OUTPUT_CHARS = 3000

interface StepInfo {
  phase: MigratePhase
  detail: string
}

const phaseLabels: Record<MigratePhase, string> = {
  idle: '',
  analyze: 'Scanning codebase',
  llm: 'Generating project plan',
  save: 'Creating project',
  done: 'Done!',
  error: 'Error',
}

export default function MigratePage() {
  const navigate = useNavigate()
  const addToast = useToastStore((s) => s.addToast)
  const [repoPath, setRepoPath] = useState('')
  const [projectName, setProjectName] = useState('')
  const [currentStep, setCurrentStep] = useState<StepInfo>({ phase: 'idle', detail: '' })
  const [llmProgress, setLlmProgress] = useState(0)
  const abortRef = useRef<AbortController | null>(null)

  // Folder browser state
  const [browserOpen, setBrowserOpen] = useState(false)
  const [browsePath, setBrowsePath] = useState('/')

  const browseQuery = useQuery({
    queryKey: ['browse', browsePath],
    queryFn: () => architectApi.browse(browsePath),
    enabled: browserOpen,
  })

  const openBrowser = useCallback(() => {
    setBrowsePath(repoPath || '/')
    setBrowserOpen(true)
  }, [repoPath])

  const selectFolder = useCallback((path: string) => {
    setRepoPath(path)
    setBrowserOpen(false)
  }, [])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!repoPath.trim()) return
    startMigration()
  }

  const startMigration = () => {
    const controller = new AbortController()
    abortRef.current = controller
    setCurrentStep({ phase: 'analyze', detail: 'Starting...' })
    setLlmProgress(0)

    const baseUrl = import.meta.env.VITE_API_URL || ''
    const body: Record<string, string> = { repo_path: repoPath.trim() }
    if (projectName.trim()) body.name = projectName.trim()

    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    const token = useAuthStore.getState().accessToken
    if (token) headers['Authorization'] = `Bearer ${token}`

    fetch(`${baseUrl}/api/v1/architect/migrate/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const text = await response.text()
          setCurrentStep({ phase: 'error', detail: `HTTP ${response.status}: ${text}` })
          return
        }
        await parseSSEStream(response, handleSSEEvent)
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          setCurrentStep({ phase: 'error', detail: err.message || 'Connection failed' })
        }
      })
  }

  const handleSSEEvent = (event: string, data: string) => {
    try {
      switch (event) {
        case 'step': {
          const info = JSON.parse(data) as { phase: string; detail: string }
          setCurrentStep({ phase: info.phase as MigratePhase, detail: info.detail })
          break
        }
        case 'llm_chunk': {
          setLlmProgress(parseInt(data, 10))
          break
        }
        case 'done': {
          const result = JSON.parse(data) as { project_id: string; name: string }
          setCurrentStep({ phase: 'done', detail: result.name })
          addToast(`Project "${result.name}" imported successfully`, 'success')
          setTimeout(() => navigate(`/projects/${result.project_id}`), 500)
          break
        }
        case 'error': {
          setCurrentStep({ phase: 'error', detail: data })
          addToast(data, 'error')
          break
        }
      }
    } catch {
      setCurrentStep({ phase: 'error', detail: 'Failed to parse server response' })
    }
  }

  const handleCancel = () => {
    abortRef.current?.abort()
    setCurrentStep({ phase: 'idle', detail: '' })
  }

  const isProcessing = currentStep.phase !== 'idle' && currentStep.phase !== 'error'

  return (
    <>
      <Header title="Import Existing Project" />
      <div className="p-8 max-w-2xl mx-auto">
        <div className="rounded-xl border border-stitch-outline-variant/10 bg-stitch-surface-container p-8">
          <p className="text-sm text-text-secondary mb-6">
            Provide a path to an existing project folder. BSNexus will analyze the codebase
            and create a project management structure with phases and tasks for future work.
          </p>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label htmlFor="repo-path" className="block text-sm font-medium text-text-primary mb-1.5">
                Project Path <span className="text-stitch-error">*</span>
              </label>
              <div className="flex gap-2">
                <input
                  id="repo-path"
                  type="text"
                  value={repoPath}
                  onChange={(e) => setRepoPath(e.target.value)}
                  placeholder="/path/to/your/project"
                  disabled={isProcessing}
                  className="flex-1 rounded-md border border-stitch-outline-variant/20 bg-stitch-surface-low px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary focus:border-stitch-primary focus:outline-none focus:ring-1 focus:ring-stitch-primary disabled:opacity-50"
                  autoFocus
                />
                <Button type="button" variant="secondary" disabled={isProcessing} onClick={openBrowser}>
                  <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>folder_open</span>
                </Button>
              </div>
            </div>

            <div>
              <label htmlFor="project-name" className="block text-sm font-medium text-text-primary mb-1.5">
                Project Name <span className="text-text-tertiary">(optional, auto-detected from folder)</span>
              </label>
              <input
                id="project-name"
                type="text"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder="My Project"
                disabled={isProcessing}
                className="w-full rounded-md border border-stitch-outline-variant/20 bg-stitch-surface-low px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary focus:border-stitch-primary focus:outline-none focus:ring-1 focus:ring-stitch-primary disabled:opacity-50"
              />
            </div>

            {isProcessing ? (
              <div className="space-y-3 py-4">
                {/* Step indicators */}
                <div className="flex items-center gap-3">
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-stitch-primary border-t-transparent" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">
                      {phaseLabels[currentStep.phase] || currentStep.phase}
                    </div>
                    <div className="text-xs text-text-tertiary">{currentStep.detail}</div>
                  </div>
                </div>

                {/* Progress bar for LLM phase */}
                {currentStep.phase === 'llm' && llmProgress > 0 && (
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-1.5 rounded-full bg-stitch-surface-lowest overflow-hidden">
                      <div
                        className="h-full bg-stitch-primary transition-all duration-300"
                        style={{ width: `${Math.min((llmProgress / ESTIMATED_LLM_OUTPUT_CHARS) * 100, 95)}%` }}
                      />
                    </div>
                    <span className="text-xs text-text-tertiary">{(llmProgress / 1000).toFixed(1)}k chars</span>
                  </div>
                )}

                {/* Phase progress dots */}
                <div className="flex items-center gap-1.5 pt-1">
                  {(['analyze', 'llm', 'save'] as const).map((phase) => {
                    const phases = ['analyze', 'llm', 'save', 'done']
                    const currentIdx = phases.indexOf(currentStep.phase)
                    const phaseIdx = phases.indexOf(phase)
                    const isDone = currentIdx > phaseIdx
                    const isCurrent = currentStep.phase === phase
                    return (
                      <div
                        key={phase}
                        className={`h-2 flex-1 rounded-full transition-colors ${
                          isDone ? 'bg-stitch-primary' : isCurrent ? 'bg-stitch-secondary' : 'bg-stitch-surface-lowest'
                        }`}
                      />
                    )
                  })}
                </div>

                <Button variant="secondary" size="sm" type="button" onClick={handleCancel} className="mt-2">
                  Cancel
                </Button>
              </div>
            ) : currentStep.phase === 'error' ? (
              <div className="space-y-3 py-4">
                <div className="rounded-md border border-stitch-error/30 bg-stitch-error-container/10 p-3">
                  <div className="text-sm font-medium text-stitch-error">Migration failed</div>
                  <div className="text-xs text-stitch-error/70 mt-1">{currentStep.detail}</div>
                </div>
                <div className="flex items-center gap-3">
                  <Button type="submit" disabled={!repoPath.trim()}>
                    Retry
                  </Button>
                  <Button variant="secondary" type="button" onClick={() => navigate('/dashboard')}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-3 pt-2">
                <Button type="submit" disabled={!repoPath.trim()}>
                  Analyze & Import
                </Button>
                <Button variant="secondary" type="button" onClick={() => navigate('/dashboard')}>
                  Cancel
                </Button>
              </div>
            )}
          </form>
        </div>
      </div>

      {/* Folder Browser Modal */}
      <Modal
        open={browserOpen}
        onClose={() => setBrowserOpen(false)}
        title="Select Project Folder"
        width={560}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setBrowserOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => selectFolder(browsePath)}>
              Select This Folder
            </Button>
          </>
        }
      >
        <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-md bg-stitch-surface-low text-sm font-mono text-text-primary">
          {browseQuery.data?.has_git && (
            <span className="material-symbols-outlined text-stitch-primary shrink-0" style={{ fontSize: '14px' }}>commit</span>
          )}
          <span className="truncate">{browsePath}</span>
        </div>

        <div className="border border-stitch-outline-variant/10 rounded-md max-h-80 overflow-y-auto">
          {browseQuery.data?.parent && (
            <button
              onClick={() => setBrowsePath(browseQuery.data!.parent!)}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-text-secondary hover:bg-stitch-surface-container transition-colors border-b border-stitch-outline-variant/10"
            >
              <span className="material-symbols-outlined text-text-tertiary shrink-0" style={{ fontSize: '16px' }}>drive_folder_upload</span>
              <span>..</span>
            </button>
          )}
          {browseQuery.isLoading && <div className="px-3 py-6 text-center text-sm text-text-tertiary">Loading...</div>}
          {browseQuery.isError && (
            <div className="px-3 py-6 text-center text-sm text-stitch-error">
              {(browseQuery.error as Error & { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
                'Failed to browse directory'}
            </div>
          )}
          {browseQuery.data?.directories.length === 0 && !browseQuery.isLoading && (
            <div className="px-3 py-6 text-center text-sm text-text-tertiary">No subdirectories</div>
          )}
          {browseQuery.data?.directories.map((dir) => (
            <button
              key={dir.path}
              onClick={() => setBrowsePath(dir.path)}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-text-primary hover:bg-stitch-surface-container transition-colors border-b border-stitch-outline-variant/10 last:border-b-0"
            >
              <span className="material-symbols-outlined text-stitch-primary shrink-0" style={{ fontSize: '16px' }}>folder</span>
              <span className="truncate text-left">{dir.name}</span>
            </button>
          ))}
        </div>
      </Modal>
    </>
  )
}
