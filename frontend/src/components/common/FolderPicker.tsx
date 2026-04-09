import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { architectApi } from '../../api/architect'
import { Button, Modal } from './index'

interface FolderPickerProps {
  value: string
  onChange: (path: string) => void
  placeholder?: string
  className?: string
}

export default function FolderPicker({ value, onChange, placeholder, className }: FolderPickerProps) {
  const [open, setOpen] = useState(false)
  const [browsePath, setBrowsePath] = useState(value || '/')

  const browseQuery = useQuery({
    queryKey: ['browse-dir', browsePath],
    queryFn: () => architectApi.browse(browsePath),
    enabled: open,
  })

  const selectFolder = (path: string) => {
    onChange(path)
    setOpen(false)
  }

  return (
    <>
      <div className={`flex gap-2 ${className || ''}`}>
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder || '/home/user/projects/my-app'}
          className="flex-1 px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-primary text-sm placeholder:text-text-tertiary focus:outline-none focus:border-stitch-primary focus:ring-1 focus:ring-stitch-primary"
        />
        <button
          type="button"
          onClick={() => { setBrowsePath(value || '/'); setOpen(true) }}
          className="px-3 py-2 bg-stitch-surface-low border border-stitch-outline-variant/20 rounded-md text-text-secondary hover:text-text-primary hover:border-stitch-primary transition-colors"
          title="Browse folders"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>folder_open</span>
        </button>
      </div>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Select Folder"
        width={560}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
            <Button size="sm" onClick={() => selectFolder(browsePath)}>Select This Folder</Button>
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
              {(browseQuery.error as Error & { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Failed to browse directory'}
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
