import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { Badge } from '../common/Badge'
import { I } from '../../lib/icons'
import {
  workspaceFilesApi,
  type WorkspaceFileEntry,
} from '../../api/workspaceFiles'

type ViewerType = 'code' | 'md' | 'data' | 'html' | 'other'

interface Props {
  projectId: string
  projectName: string
}

/**
 * Files tab — left tree + right viewer. Reads the project workspace
 * directory directly (``GET /api/v1/projects/{id}/files``). A workspace
 * entry is created for every fenced code block in a run's output, so
 * the file tree grows as the company ships.
 */
export default function FilesView({ projectId, projectName }: Props) {
  const { data: entries = [], isLoading } = useQuery<WorkspaceFileEntry[]>({
    queryKey: ['workspace-files', projectId],
    queryFn: () => workspaceFilesApi.list(projectId),
    refetchInterval: 3000,
  })

  // Track the user's explicit pick separately from the effective
  // selection. Effective selection is derived during render so we
  // don't need a setState-in-effect to auto-pick the first entry.
  const [manualSelected, setManualSelected] = useState<string | null>(null)
  const selected =
    manualSelected && entries.some((e) => e.path === manualSelected)
      ? manualSelected
      : entries[0]?.path ?? null

  if (isLoading && entries.length === 0) {
    return (
      <div style={{ padding: 32, color: 'var(--text-tertiary)', fontSize: 13 }}>
        Loading…
      </div>
    )
  }

  if (entries.length === 0) {
    return (
      <div style={{ padding: 32, color: 'var(--text-tertiary)', fontSize: 13 }}>
        No files yet. Send a direction in the chat and the company will start
        shipping files here.
      </div>
    )
  }

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '260px 1fr',
        height: '100%',
        minHeight: 0,
      }}
    >
      <nav
        style={{
          borderRight: '1px solid var(--border-subtle)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div
          style={{
            padding: '10px 12px',
            borderBottom: '1px solid var(--border-subtle)',
            fontSize: 10,
            color: 'var(--text-tertiary)',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}
        >
          {projectName} · files · {entries.length}
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
          <FileTree entries={entries} selected={selected} onSelect={setManualSelected} />
        </div>
      </nav>

      <div
        style={{
          overflow: 'hidden',
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {selected && <FileViewer projectId={projectId} path={selected} />}
      </div>
    </div>
  )
}

interface TreeNode {
  name: string
  fullPath: string
  children: Map<string, TreeNode>
  size: number | null
}

function FileTree({
  entries,
  selected,
  onSelect,
}: {
  entries: WorkspaceFileEntry[]
  selected: string | null
  onSelect: (p: string) => void
}) {
  const root = useMemo(() => buildTree(entries), [entries])
  return <>{renderTree(root, 0, selected, onSelect)}</>
}

function buildTree(entries: WorkspaceFileEntry[]): TreeNode {
  const root: TreeNode = {
    name: '',
    fullPath: '',
    children: new Map(),
    size: null,
  }
  for (const e of entries) {
    const parts = e.path.split('/')
    let cur = root
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i]
      const isLeaf = i === parts.length - 1
      const key = parts.slice(0, i + 1).join('/')
      if (!cur.children.has(p)) {
        cur.children.set(p, {
          name: p,
          fullPath: key,
          children: new Map(),
          size: isLeaf ? e.size : null,
        })
      }
      cur = cur.children.get(p)!
    }
  }
  return root
}

function renderTree(
  node: TreeNode,
  depth: number,
  selected: string | null,
  onSelect: (p: string) => void,
): React.ReactNode {
  const kids = [...node.children.values()].sort((a, b) => {
    const aDir = a.children.size > 0
    const bDir = b.children.size > 0
    if (aDir !== bDir) return aDir ? -1 : 1
    return a.name.localeCompare(b.name)
  })
  return (
    <>
      {kids.map((c) => {
        const isDir = c.children.size > 0
        if (isDir) {
          return (
            <div key={c.fullPath}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '3px 8px',
                  paddingLeft: 8 + depth * 14,
                  fontSize: 12,
                  color: 'var(--gray-400)',
                }}
              >
                <I.ChevDown size={10} />
                <span>{c.name}</span>
              </div>
              {renderTree(c, depth + 1, selected, onSelect)}
            </div>
          )
        }
        const isActive = selected === c.fullPath
        return (
          <button
            key={c.fullPath}
            type="button"
            onClick={() => onSelect(c.fullPath)}
            className={`sb-item ${isActive ? 'active' : ''}`}
            style={{
              paddingLeft: 8 + depth * 14,
              margin: '1px 0',
              fontSize: 12,
            }}
          >
            <FileIcon path={c.name} />
            <span className="label">{c.name}</span>
            {c.size != null && (
              <span className="mono faded" style={{ fontSize: 10 }}>
                {formatSize(c.size)}
              </span>
            )}
          </button>
        )
      })}
    </>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`
}

function classifyType(path: string): ViewerType {
  const p = path.toLowerCase()
  if (p.endsWith('.md') || p.endsWith('.markdown')) return 'md'
  if (p.endsWith('.json') || p.endsWith('.yml') || p.endsWith('.yaml')) return 'data'
  if (p.endsWith('.html') || p.endsWith('.htm')) return 'html'
  if (
    /\.(py|ts|tsx|jsx|js|mjs|css|scss|sass|sh|bash|rs|go|java|kt|swift|sql|xml|env|toml)$/.test(p)
  )
    return 'code'
  if (/(^|\/)Dockerfile(\.|$)/.test(path)) return 'code'
  return 'other'
}

function langFromPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  if (/^(tsx|jsx)$/.test(ext)) return ext
  if (ext === 'py') return 'python'
  if (ext === 'ts') return 'typescript'
  if (ext === 'js') return 'javascript'
  if (ext === 'sh' || ext === 'bash') return 'bash'
  if (ext === 'yml' || ext === 'yaml') return 'yaml'
  if (/(^|\/)Dockerfile(\.|$)/.test(path)) return 'dockerfile'
  return ext || 'text'
}

export function FileIcon({ path }: { path: string }) {
  const kind = classifyType(path)
  const glyph =
    kind === 'md' ? (
      <I.Doc size={12} />
    ) : kind === 'data' ? (
      <I.Data size={12} />
    ) : kind === 'html' ? (
      <I.Design size={12} />
    ) : (
      <I.Code size={12} />
    )
  const color: Record<ViewerType, string> = {
    md: '#6ee7b7',
    data: '#fcd34d',
    html: '#c4b5fd',
    code: '#93c5fd',
    other: '#9ca3af',
  }
  return <span style={{ color: color[kind], flex: 'none' }}>{glyph}</span>
}

function FileViewer({ projectId, path }: { projectId: string; path: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['workspace-file', projectId, path],
    queryFn: () => workspaceFilesApi.read(projectId, path),
    refetchInterval: 3000,
  })

  const kind = classifyType(path)
  const content = data?.content ?? ''

  return (
    <>
      <div
        style={{
          padding: '10px 16px',
          borderBottom: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          background: 'var(--bg-surface)',
        }}
      >
        <FileIcon path={path} />
        <span className="mono" style={{ fontSize: 12, color: 'var(--gray-100)' }}>
          {path}
        </span>
        <Badge tone="gray" square>
          {kind}
        </Badge>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => content && navigator.clipboard.writeText(content)}
        >
          <I.Copy size={12} /> Copy
        </button>
      </div>
      <div
        style={{
          flex: 1,
          overflow: 'auto',
          minHeight: 0,
          background: 'var(--bg-surface)',
        }}
      >
        {isLoading && (
          <div style={{ padding: 24, color: 'var(--text-tertiary)' }}>Loading…</div>
        )}
        {!isLoading && kind === 'md' && (
          <div className="md" style={{ padding: 16, color: 'var(--gray-100)' }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        )}
        {!isLoading && kind !== 'md' && (
          <pre
            className="mono"
            style={{
              margin: 0,
              padding: 16,
              fontSize: 12,
              lineHeight: '18px',
              color: 'var(--gray-100)',
              whiteSpace: 'pre',
            }}
          >
            <code className={`lang-${langFromPath(path)}`}>{content}</code>
          </pre>
        )}
      </div>
    </>
  )
}
