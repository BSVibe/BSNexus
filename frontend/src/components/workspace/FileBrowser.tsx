import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { workspaceApi } from '../../api/workspace'
import type { FileInfo } from '../../types/workspace'
import GitHubConnect from './GitHubConnect'

const EXT_ICONS: Record<string, string> = {
  py: 'code',
  ts: 'javascript',
  tsx: 'javascript',
  js: 'javascript',
  jsx: 'javascript',
  json: 'data_object',
  md: 'description',
  yaml: 'settings',
  yml: 'settings',
  toml: 'settings',
  sh: 'terminal',
  sql: 'storage',
  html: 'web',
  css: 'palette',
}

function getIcon(file: FileInfo): string {
  if (file.is_dir) return 'folder'
  const ext = file.name.split('.').pop()?.toLowerCase() || ''
  return EXT_ICONS[ext] || 'draft'
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

interface FileBrowserProps {
  projectId: string
}

export default function FileBrowser({ projectId }: FileBrowserProps) {
  const [currentPath, setCurrentPath] = useState('')
  const [selectedFile, setSelectedFile] = useState<string | null>(null)

  const { data: listing, isLoading } = useQuery({
    queryKey: ['workspace-files', projectId, currentPath],
    queryFn: () => workspaceApi.listFiles(projectId, currentPath),
  })

  const { data: fileContent, isLoading: contentLoading } = useQuery({
    queryKey: ['workspace-file-content', projectId, selectedFile],
    queryFn: () => workspaceApi.readFile(projectId, selectedFile!),
    enabled: !!selectedFile,
  })

  const navigateTo = (path: string) => {
    setCurrentPath(path)
    setSelectedFile(null)
  }

  const handleClick = (file: FileInfo) => {
    if (file.is_dir) {
      navigateTo(file.path)
    } else {
      setSelectedFile(file.path)
    }
  }

  const pathParts = currentPath ? currentPath.split('/') : []

  return (
    <div className="flex h-full">
      {/* File tree panel */}
      <div className="w-72 border-r border-stitch-outline-variant/10 overflow-y-auto flex flex-col">
        {/* GitHub connection */}
        <div className="p-2 border-b border-stitch-outline-variant/10">
          <GitHubConnect projectId={projectId} />
        </div>
        {/* Breadcrumb */}
        <div className="flex items-center gap-1 px-3 py-2 border-b border-stitch-outline-variant/10 text-xs text-text-tertiary">
          <button onClick={() => navigateTo('')} className="hover:text-text-primary transition-colors">
            /
          </button>
          {pathParts.map((part, i) => {
            const path = pathParts.slice(0, i + 1).join('/')
            return (
              <span key={path} className="flex items-center gap-1">
                <span>/</span>
                <button onClick={() => navigateTo(path)} className="hover:text-text-primary transition-colors">
                  {part}
                </button>
              </span>
            )
          })}
        </div>

        {/* Back button */}
        {currentPath && (
          <button
            onClick={() => navigateTo(currentPath.split('/').slice(0, -1).join('/'))}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-tertiary hover:bg-stitch-surface-container transition-colors"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>arrow_back</span>
            ..
          </button>
        )}

        {/* File list */}
        {isLoading ? (
          <div className="p-4 text-xs text-text-tertiary">Loading...</div>
        ) : !listing?.files.length ? (
          <div className="p-4 text-center">
            <span className="material-symbols-outlined text-2xl text-text-tertiary mb-2 block">folder_open</span>
            <p className="text-xs text-text-tertiary">Empty directory</p>
          </div>
        ) : (
          <div>
            {listing.files.map((file) => (
              <button
                key={file.path}
                onClick={() => handleClick(file)}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-left transition-colors ${
                  selectedFile === file.path
                    ? 'bg-stitch-primary/10 text-stitch-primary'
                    : 'hover:bg-stitch-surface-container text-text-secondary'
                }`}
              >
                <span
                  className="material-symbols-outlined shrink-0"
                  style={{ fontSize: '14px', color: file.is_dir ? 'var(--color-warning)' : undefined }}
                >
                  {getIcon(file)}
                </span>
                <span className="text-xs truncate flex-1">{file.name}</span>
                {!file.is_dir && (
                  <span className="text-[10px] text-text-tertiary shrink-0">{formatSize(file.size)}</span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Content panel */}
      <div className="flex-1 overflow-auto">
        {selectedFile ? (
          contentLoading ? (
            <div className="p-4 text-xs text-text-tertiary">Loading file...</div>
          ) : fileContent ? (
            <div className="h-full flex flex-col">
              <div className="flex items-center justify-between px-4 py-2 border-b border-stitch-outline-variant/10">
                <span className="text-xs text-text-secondary font-mono">{selectedFile}</span>
                <span className="text-[10px] text-text-tertiary">{formatSize(fileContent.size)}</span>
              </div>
              {fileContent.is_binary ? (
                <div className="flex-1 flex items-center justify-center text-text-tertiary text-sm">
                  Binary file — {formatSize(fileContent.size)}
                </div>
              ) : (
                <pre className="flex-1 p-4 text-xs font-mono text-text-primary whitespace-pre-wrap overflow-auto leading-relaxed">
                  {fileContent.content}
                </pre>
              )}
            </div>
          ) : null
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-tertiary text-sm">
            Select a file to view its contents
          </div>
        )}
      </div>
    </div>
  )
}
