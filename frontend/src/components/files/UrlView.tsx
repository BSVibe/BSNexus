import { I } from '../../lib/icons'

export default function UrlView({ url }: { url: string }) {
  return (
    <div style={{ padding: 16, height: '100%', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: 8,
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--r-md)',
        }}
      >
        <I.Url size={14} />
        <span className="mono" style={{ fontSize: 12, color: 'var(--gray-100)' }}>
          {url}
        </span>
        <span style={{ flex: 1 }} />
        <a
          className="btn btn-secondary btn-sm"
          href={url}
          target="_blank"
          rel="noreferrer"
        >
          Open
        </a>
      </div>
      <div
        style={{
          flex: 1,
          border: '1px dashed var(--border-subtle)',
          borderRadius: 'var(--r-md)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-tertiary)',
          fontSize: 13,
        }}
      >
        [preview embed — {url}]
      </div>
    </div>
  )
}
