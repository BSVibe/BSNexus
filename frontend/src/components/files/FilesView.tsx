import { useMemo, useState } from 'react'

import { Badge } from '../common/Badge'
import { I } from '../../lib/icons'
import { SAMPLE_FILES } from '../../lib/bsd-sample'
import type { BsdDocument, FileType, ProjectFile } from '../../lib/bsd-types'
import BsdViewer from './BsdViewer'
import CodeView from './CodeView'
import MdView from './MdView'
import UrlView from './UrlView'

/**
 * Files tab — left tree + right viewer. Mirrors the Pencil-Dev /
 * Stitch layout from the Claude Design bundle.
 *
 * Data is sampled from ``lib/bsd-sample.ts`` until the backend exposes
 * ``GET /api/v1/projects/{id}/files``.
 */
export default function FilesView({ projectName }: { projectName: string }) {
  const files = SAMPLE_FILES
  const [selectedPath, setSelectedPath] = useState<string>(files[0]?.path ?? '')
  const [bulkOpen, setBulkOpen] = useState(false)

  const file = files.find((f) => f.path === selectedPath)

  if (files.length === 0) {
    return (
      <div style={{ padding: 32, color: 'var(--text-tertiary)' }}>No files yet.</div>
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
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 12px',
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <div
            style={{
              fontSize: 10,
              color: 'var(--text-tertiary)',
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {projectName} · files
          </div>
          <div style={{ position: 'relative' }}>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setBulkOpen((v) => !v)}
              title="Export all files"
            >
              <I.Download size={12} /> Export all
            </button>
            {bulkOpen && (
              <>
                <div
                  onClick={() => setBulkOpen(false)}
                  style={{ position: 'fixed', inset: 0, zIndex: 50 }}
                />
                <div
                  style={{
                    position: 'absolute',
                    top: 'calc(100% + 6px)',
                    right: 0,
                    minWidth: 240,
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--r-md)',
                    boxShadow: '0 12px 40px rgba(0,0,0,0.4)',
                    padding: 4,
                    zIndex: 51,
                  }}
                >
                  <div
                    style={{
                      padding: '6px 10px',
                      fontSize: 10,
                      color: 'var(--text-tertiary)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.08em',
                    }}
                  >
                    Bundle · {files.length} files
                  </div>
                  {[
                    { id: 'zip', label: 'ZIP archive', hint: 'all files' },
                    { id: 'tar', label: 'tar.gz', hint: 'all files' },
                    { id: 'gh', label: 'Push to GitHub repo…' },
                    { id: 'drive', label: 'Send to Google Drive' },
                    { id: 'handoff', label: 'Handoff package', hint: 'readme + files' },
                  ].map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      className="sb-item"
                      style={{ width: '100%', padding: '8px 10px', fontSize: 12 }}
                      onClick={() => setBulkOpen(false)}
                    >
                      <span className="label">{opt.label}</span>
                      {opt.hint && (
                        <span className="mono faded" style={{ fontSize: 10 }}>
                          {opt.hint}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
          <BsdFileTree
            files={files}
            selected={selectedPath}
            onSelect={setSelectedPath}
          />
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
        {file && <FileViewer file={file} />}
      </div>
    </div>
  )
}

interface TreeNode {
  name: string
  children: Record<string, TreeNode>
  files: ProjectFile[]
}

function BsdFileTree({
  files,
  selected,
  onSelect,
}: {
  files: ProjectFile[]
  selected: string
  onSelect: (path: string) => void
}) {
  const tree = useMemo(() => {
    const root: TreeNode = { name: '', children: {}, files: [] }
    files.forEach((f) => {
      const parts = f.path.split('/')
      let node = root
      for (let i = 0; i < parts.length - 1; i++) {
        if (!node.children[parts[i]]) {
          node.children[parts[i]] = { name: parts[i], children: {}, files: [] }
        }
        node = node.children[parts[i]]
      }
      node.files.push(f)
    })
    return root
  }, [files])

  return <div>{renderNode(tree, 0, selected, onSelect)}</div>
}

function renderNode(
  node: TreeNode,
  depth: number,
  selected: string,
  onSelect: (path: string) => void,
): React.ReactNode {
  return (
    <>
      {Object.values(node.children).map((c) => (
        <div key={c.name}>
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
          {renderNode(c, depth + 1, selected, onSelect)}
        </div>
      ))}
      {node.files.map((f) => (
        <button
          key={f.path}
          type="button"
          onClick={() => onSelect(f.path)}
          className={`sb-item ${selected === f.path ? 'active' : ''}`}
          style={{ paddingLeft: 8 + depth * 14, margin: '1px 0', fontSize: 12 }}
        >
          <FileIcon type={f.type} />
          <span className="label">{f.path.split('/').pop()}</span>
          {f.size && (
            <span className="mono faded" style={{ fontSize: 10 }}>
              {f.size}
            </span>
          )}
        </button>
      ))}
    </>
  )
}

export function FileIcon({ type }: { type: FileType }) {
  const glyph: Record<FileType, React.ReactNode> = {
    bsd: <I.Design size={12} />,
    md: <I.Doc size={12} />,
    code: <I.Code size={12} />,
    data: <I.Data size={12} />,
    url: <I.Url size={12} />,
  }
  const color: Record<FileType, string> = {
    bsd: '#c4b5fd',
    md: '#6ee7b7',
    code: '#93c5fd',
    data: '#fcd34d',
    url: '#93c5fd',
  }
  return <span style={{ color: color[type], flex: 'none' }}>{glyph[type]}</span>
}

function FileViewer({ file }: { file: ProjectFile }) {
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
          position: 'relative',
        }}
      >
        <FileIcon type={file.type} />
        <span className="mono" style={{ fontSize: 12, color: 'var(--gray-100)' }}>
          {file.path}
        </span>
        <Badge tone="gray" square>
          {file.type}
        </Badge>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => navigator.clipboard.writeText(file.path)}
        >
          <I.Copy size={12} /> Copy path
        </button>
      </div>
      <div
        style={{
          flex: 1,
          overflow: 'auto',
          minHeight: 0,
          background: file.type === 'bsd' ? 'var(--bg-base)' : 'var(--bg-surface)',
        }}
      >
        {file.type === 'bsd' && file.bsd && <BsdViewer bsd={file.bsd as BsdDocument} />}
        {file.type === 'md' && <MdView content={file.content ?? ''} />}
        {file.type === 'code' && (
          <CodeView content={file.content ?? ''} lang={file.lang ?? 'text'} />
        )}
        {file.type === 'data' && <CodeView content={file.content ?? ''} lang="json" />}
        {file.type === 'url' && <UrlView url={file.content ?? ''} />}
      </div>
    </>
  )
}
