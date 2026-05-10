'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { useQuery } from '@tanstack/react-query'

import { I } from '../../lib/icons'
import {
  workspaceFilesApi,
  type WorkspaceContentResponse,
  type WorkspaceEntry,
  type WorkspaceTreeResponse,
} from '../../api/workspaceFiles'

/**
 * WorkspaceIndex — read-only workspace tree + file content viewer.
 *
 * G7.5c: founder needs file/folder visibility for design + code
 * reviews. Without this, deliverables that reference paths in the
 * project workspace (artifact_refs) have nowhere to land.
 *
 * Layout: tree on the left, content panel on the right. On mobile
 * (<768) the tree is the default view and selecting a file swaps to
 * a content view with a back button.
 *
 * Deep link: a parent surface (e.g. DeliverableCard) can pass
 * `initialPath` to pre-select a file via ?path=.
 */
export default function WorkspaceIndex({
  projectId,
  initialPath,
}: {
  projectId: string
  initialPath?: string | null
}) {
  const t = useTranslations('nexus.workspace')

  // Resolve the directory we're listing + the file we're viewing.
  // ``initialPath`` may point at either; if it's a file we list its
  // parent and select the file. Re-deriving when ``initialPath``
  // changes is handled by the "track-the-prop" pattern below — the
  // browser router replaces this whole tree on tab change anyway.
  const [seenInitialPath, setSeenInitialPath] = useState<string | null | undefined>(initialPath)
  const [dirPath, setDirPath] = useState<string>(() => splitPath(initialPath).dir)
  const [selectedFile, setSelectedFile] = useState<string | null>(() => splitPath(initialPath).file)

  if (initialPath !== seenInitialPath) {
    const next = splitPath(initialPath)
    setSeenInitialPath(initialPath)
    setDirPath(next.dir)
    setSelectedFile(next.file)
  }

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(220px, 280px) 1fr',
        gap: 12,
        padding: 12,
        height: '100%',
        minHeight: 0,
      }}
      className="workspace-index"
    >
      <TreePanel
        projectId={projectId}
        dirPath={dirPath}
        selectedFile={selectedFile}
        onNavigate={(nextDir, file) => {
          setDirPath(nextDir)
          setSelectedFile(file)
        }}
        t={t}
      />
      <ContentPanel
        projectId={projectId}
        selectedFile={selectedFile}
        t={t}
      />
    </div>
  )
}

interface TreePanelProps {
  projectId: string
  dirPath: string
  selectedFile: string | null
  onNavigate: (dir: string, file: string | null) => void
  t: (k: string) => string
}

function TreePanel({ projectId, dirPath, selectedFile, onNavigate, t }: TreePanelProps) {
  const { data, isLoading, isError } = useQuery<WorkspaceTreeResponse>({
    queryKey: ['workspace-files', projectId, dirPath],
    queryFn: () => workspaceFilesApi.tree(projectId, dirPath),
  })

  return (
    <div
      className="card"
      style={{
        padding: 8,
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '4px 6px',
          fontSize: 11,
          color: 'var(--text-secondary)',
        }}
      >
        <I.Doc size={11} />
        <span className="mono" title={dirPath || '/'}>
          {dirPath || '/'}
        </span>
      </div>

      {dirPath && (
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => onNavigate(parentOf(dirPath), null)}
          style={{ justifyContent: 'flex-start', minHeight: 36 }}
        >
          <I.ChevLeft size={12} />
          <span style={{ marginLeft: 4 }}>{t('upDirectory')}</span>
        </button>
      )}

      {isLoading && (
        <div className="faded" style={{ padding: 12, fontSize: 12 }}>
          {t('loading')}
        </div>
      )}
      {isError && (
        <div style={{ padding: 12, fontSize: 12, color: 'var(--rose-500)' }}>
          {t('treeError')}
        </div>
      )}
      {data && data.entries.length === 0 && !isLoading && (
        <div className="faded" style={{ padding: 12, fontSize: 12 }}>
          {t('emptyDir')}
        </div>
      )}

      {data?.entries.map((entry) => (
        <EntryRow
          key={entry.name}
          entry={entry}
          isSelected={
            entry.kind === 'file' &&
            selectedFile === joinPath(dirPath, entry.name)
          }
          onClick={() => {
            const fullPath = joinPath(dirPath, entry.name)
            if (entry.kind === 'dir') {
              onNavigate(fullPath, null)
            } else {
              onNavigate(dirPath, fullPath)
            }
          }}
        />
      ))}
    </div>
  )
}

function EntryRow({
  entry,
  isSelected,
  onClick,
}: {
  entry: WorkspaceEntry
  isSelected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="btn btn-ghost btn-sm"
      style={{
        justifyContent: 'flex-start',
        minHeight: 36,
        background: isSelected ? 'var(--bg-hover)' : 'transparent',
        color: isSelected ? 'var(--gray-50)' : 'var(--gray-200)',
        gap: 6,
      }}
    >
      {entry.kind === 'dir' ? <I.ChevRight size={12} /> : <I.Doc size={12} />}
      <span
        style={{
          flex: 1,
          textAlign: 'left',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {entry.name}
      </span>
      {entry.kind === 'file' && entry.size != null && (
        <span className="mono faded" style={{ fontSize: 10 }}>
          {humanSize(entry.size)}
        </span>
      )}
    </button>
  )
}

interface ContentPanelProps {
  projectId: string
  selectedFile: string | null
  t: (k: string) => string
}

function ContentPanel({ projectId, selectedFile, t }: ContentPanelProps) {
  const { data, isLoading, error } = useQuery<WorkspaceContentResponse>({
    queryKey: ['workspace-files-content', projectId, selectedFile],
    queryFn: () => workspaceFilesApi.content(projectId, selectedFile!),
    enabled: Boolean(selectedFile),
  })

  if (!selectedFile) {
    return (
      <div
        className="card"
        style={{
          padding: 24,
          fontSize: 13,
          color: 'var(--text-tertiary)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: 0,
        }}
      >
        {t('selectFileHint')}
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="card" style={{ padding: 24, fontSize: 13, color: 'var(--text-tertiary)' }}>
        {t('loading')}
      </div>
    )
  }

  if (error) {
    const status = (error as { response?: { status?: number } }).response?.status
    const message =
      status === 415
        ? t('binaryNotSupported')
        : status === 413
        ? t('fileTooLarge')
        : t('contentError')
    return (
      <div className="card" style={{ padding: 24, fontSize: 13, color: 'var(--rose-500)' }}>
        {message}
      </div>
    )
  }

  return (
    <div
      className="card"
      style={{
        padding: 12,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 8,
          paddingBottom: 8,
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        <I.Doc size={12} />
        <span className="mono" style={{ fontSize: 12, color: 'var(--gray-50)', flex: 1 }}>
          {selectedFile}
        </span>
        {data?.size != null && (
          <span className="mono faded" style={{ fontSize: 11 }}>
            {humanSize(data.size)}
          </span>
        )}
      </div>
      <pre
        className="mono"
        style={{
          margin: 0,
          fontSize: 12,
          lineHeight: 1.5,
          color: 'var(--gray-100)',
          overflow: 'auto',
          whiteSpace: 'pre',
          flex: 1,
          minHeight: 0,
        }}
      >
        {data?.content}
      </pre>
    </div>
  )
}

function splitPath(input?: string | null): { dir: string; file: string | null } {
  if (!input) return { dir: '', file: null }
  // Heuristic: anything with a "." in the leaf is a file, otherwise dir.
  // The backend will tell us if we're wrong (404 / 415); the UI just
  // prefers a sensible initial state.
  const leaf = input.split('/').pop() ?? ''
  const looksLikeFile = leaf.includes('.')
  if (looksLikeFile) {
    return { dir: parentOf(input), file: input }
  }
  return { dir: input, file: null }
}

function parentOf(p: string): string {
  if (!p) return ''
  const idx = p.lastIndexOf('/')
  return idx === -1 ? '' : p.slice(0, idx)
}

function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
