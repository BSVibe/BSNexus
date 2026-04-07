import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { workspaceApi } from '../../api/workspace'

const INPUT_CLASS =
  'w-full px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary'

interface GitHubConnectProps {
  projectId: string
}

export default function GitHubConnect({ projectId }: GitHubConnectProps) {
  const queryClient = useQueryClient()
  const [repoUrl, setRepoUrl] = useState('')
  const [token, setToken] = useState('')
  const [branch, setBranch] = useState('main')

  const { data: status, isLoading } = useQuery({
    queryKey: ['github-status', projectId],
    queryFn: () => workspaceApi.githubStatus(projectId),
  })

  const connectMutation = useMutation({
    mutationFn: () => workspaceApi.githubConnect(projectId, repoUrl, token, branch),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['github-status', projectId] })
      queryClient.invalidateQueries({ queryKey: ['workspace-files', projectId] })
      setToken('')
    },
  })

  const syncMutation = useMutation({
    mutationFn: () => workspaceApi.githubSync(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace-files', projectId] })
    },
  })

  const pushMutation = useMutation({
    mutationFn: () => workspaceApi.githubPush(projectId),
  })

  const disconnectMutation = useMutation({
    mutationFn: () => workspaceApi.githubDisconnect(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['github-status', projectId] })
    },
  })

  if (isLoading) return <div className="text-xs text-text-tertiary p-2">Loading...</div>

  if (status?.connected) {
    return (
      <div className="p-3 bg-stitch-surface-low rounded-lg border border-stitch-outline-variant/10 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-text-secondary" style={{ fontSize: '16px' }}>link</span>
            <span className="text-xs text-text-primary font-medium truncate">{status.repo_url}</span>
          </div>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 font-bold">Connected</span>
        </div>
        <p className="text-[10px] text-text-tertiary">Branch: {status.branch || 'main'}</p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => syncMutation.mutate()}
            disabled={syncMutation.isPending}
            className="text-[10px] px-2.5 py-1 rounded bg-stitch-primary/10 text-stitch-primary hover:bg-stitch-primary/20 transition-colors disabled:opacity-50"
          >
            {syncMutation.isPending ? 'Syncing...' : 'Pull'}
          </button>
          <button
            onClick={() => pushMutation.mutate()}
            disabled={pushMutation.isPending}
            className="text-[10px] px-2.5 py-1 rounded bg-stitch-primary/10 text-stitch-primary hover:bg-stitch-primary/20 transition-colors disabled:opacity-50"
          >
            {pushMutation.isPending ? 'Pushing...' : 'Push'}
          </button>
          <button
            onClick={() => { if (confirm('Disconnect GitHub? Workspace files will be kept.')) disconnectMutation.mutate() }}
            className="text-[10px] px-2.5 py-1 rounded bg-stitch-error/10 text-stitch-error hover:bg-stitch-error/20 transition-colors ml-auto"
          >
            Disconnect
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="p-3 bg-stitch-surface-low rounded-lg border border-stitch-outline-variant/10 space-y-3">
      <p className="text-xs text-text-secondary">Connect a GitHub repository</p>
      <input
        value={repoUrl}
        onChange={(e) => setRepoUrl(e.target.value)}
        placeholder="https://github.com/owner/repo"
        className={INPUT_CLASS}
      />
      <input
        type="password"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        placeholder="GitHub Personal Access Token"
        className={INPUT_CLASS}
      />
      <input
        value={branch}
        onChange={(e) => setBranch(e.target.value)}
        placeholder="Branch (default: main)"
        className={INPUT_CLASS}
      />
      {connectMutation.isError && (
        <p className="text-[10px] text-stitch-error">{(connectMutation.error as Error).message}</p>
      )}
      <button
        onClick={() => connectMutation.mutate()}
        disabled={!repoUrl.trim() || !token.trim() || connectMutation.isPending}
        className="w-full text-xs px-3 py-2 rounded bg-stitch-primary text-stitch-on-primary font-bold hover:opacity-90 transition-opacity disabled:opacity-50"
      >
        {connectMutation.isPending ? 'Cloning...' : 'Connect'}
      </button>
    </div>
  )
}
