import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { Project } from '../../types/project'
import { projectsApi } from '../../api/projects'
import { Badge, Button } from '../common'

type PanelPhase = 'review' | 'loading' | 'complete' | 'error'

const statusBadgeColors: Record<string, string> = {
  design: 'var(--status-queued)',
  active: 'var(--color-success)',
  paused: 'var(--color-warning)',
  completed: 'var(--status-ready)',
}

interface Props {
  designSummary: string
  onConfirm: (repoPath: string) => Promise<Project>
  onCancel: () => void
  onGoToBoard: (projectId: string) => void
  finalizedProjectId?: string | null
}

export default function FinalizePanel({ designSummary, onConfirm, onCancel, onGoToBoard, finalizedProjectId }: Props) {
  const [phase, setPhase] = useState<PanelPhase>(finalizedProjectId ? 'loading' : 'review')
  const [repoPath, setRepoPath] = useState('')
  const [project, setProject] = useState<Project | null>(null)
  const [error, setError] = useState<string | null>(null)

  // If session was already finalized, load the existing project
  useEffect(() => {
    if (!finalizedProjectId) return
    projectsApi.get(finalizedProjectId).then((p) => {
      setProject(p)
      setPhase('complete')
    }).catch(() => {
      setPhase('review')
    })
  }, [finalizedProjectId])

  const handleConfirm = async () => {
    setPhase('loading')
    setError(null)
    try {
      const result = await onConfirm(repoPath)
      setProject(result)
      setPhase('complete')
    } catch (err: unknown) {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined
      setError(detail || 'Failed to create project. Please try again.')
      setPhase('error')
    }
  }

  return (
    <div className="w-[480px] border-l border-border bg-bg-surface flex flex-col h-full shrink-0">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border-subtle">
        <h2 className="text-lg font-semibold text-text-primary">
          {phase === 'complete' ? 'Project Created' : 'Design Review'}
        </h2>
        <p className="text-xs text-text-tertiary mt-1">
          {phase === 'review' && 'Review the design specification and create the project.'}
          {phase === 'loading' && 'Creating project...'}
          {phase === 'complete' && 'Your project has been created successfully.'}
          {phase === 'error' && 'Something went wrong.'}
        </p>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {phase === 'review' && (
          <>
            <div className="prose prose-sm max-w-none mb-6">
              <ReactMarkdown
                components={{
                  code({ className, children, ...props }) {
                    const match = /language-(\w+)/.exec(className || '')
                    const codeStr = String(children).replace(/\n$/, '')
                    if (match) {
                      return (
                        <SyntaxHighlighter style={oneDark} language={match[1]} PreTag="div">
                          {codeStr}
                        </SyntaxHighlighter>
                      )
                    }
                    return <code className={className} {...props}>{children}</code>
                  },
                }}
              >
                {designSummary}
              </ReactMarkdown>
            </div>

            <div className="border-t border-border-subtle pt-4">
              <label className="block text-sm font-medium text-text-primary mb-1">
                Repository Path
              </label>
              <p className="text-xs text-text-tertiary mb-2">
                Enter the path on the server where the project will be initialized.
              </p>
              <input
                type="text"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                placeholder="/home/user/projects/my-project"
                className="w-full rounded-md border border-border bg-bg-input px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent"
              />
            </div>
          </>
        )}

        {phase === 'loading' && (
          <div className="flex flex-col items-center justify-center py-16 gap-4">
            <svg className="animate-spin h-8 w-8 text-accent" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <p className="text-sm text-text-secondary">Decomposing project into phases and tasks...</p>
          </div>
        )}

        {phase === 'complete' && project && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold text-text-primary">{project.name}</h3>
              <Badge color={statusBadgeColors[project.status] || 'var(--status-queued)'} label={project.status} />
            </div>
            <p className="text-sm text-text-secondary">{project.description}</p>

            {project.phases.map((ph) => (
              <div key={ph.id} className="border-t border-border-subtle pt-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-semibold text-text-primary">Phase {ph.order}: {ph.name}</span>
                  <Badge color="blocked" label={ph.status} />
                </div>
                {ph.description && (
                  <p className="text-xs text-text-secondary">{ph.description}</p>
                )}
              </div>
            ))}
          </div>
        )}

        {phase === 'error' && (
          <div className="py-8">
            <div className="rounded-md bg-error-muted/50 border border-error/30 px-4 py-3 text-sm text-error">
              {error}
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-6 py-4 border-t border-border-subtle flex justify-end gap-3">
        {phase === 'review' && (
          <>
            <Button variant="secondary" onClick={onCancel}>Cancel</Button>
            <Button onClick={handleConfirm} disabled={!repoPath.trim()}>
              Confirm & Create Project
            </Button>
          </>
        )}
        {phase === 'error' && (
          <>
            <Button variant="secondary" onClick={onCancel}>Cancel</Button>
            <Button onClick={() => setPhase('review')}>Retry</Button>
          </>
        )}
        {phase === 'complete' && project && (
          <Button onClick={() => onGoToBoard(project.id)}>
            Go to Board
          </Button>
        )}
      </div>
    </div>
  )
}
