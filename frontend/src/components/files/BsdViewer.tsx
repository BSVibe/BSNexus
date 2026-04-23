import { useState } from 'react'

import type { BsdDocument } from '../../lib/bsd-types'
import BsdCanvas from './BsdCanvas'

/** View / Raw toggle around the .bsd document. */
export default function BsdViewer({ bsd }: { bsd: BsdDocument }) {
  const [mode, setMode] = useState<'view' | 'raw'>('view')
  const { frames, tokens, meta } = bsd

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 16px',
          borderBottom: '1px solid var(--border-subtle)',
          background: 'var(--bg-surface)',
        }}
      >
        <div
          style={{
            display: 'inline-flex',
            background: 'var(--bg-base)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--r-md)',
            padding: 2,
          }}
        >
          <button
            type="button"
            className={`btn btn-sm ${mode === 'view' ? 'btn-secondary' : 'btn-ghost'}`}
            onClick={() => setMode('view')}
          >
            View
          </button>
          <button
            type="button"
            className={`btn btn-sm ${mode === 'raw' ? 'btn-secondary' : 'btn-ghost'}`}
            onClick={() => setMode('raw')}
          >
            Raw
          </button>
        </div>
        <span className="faded mono" style={{ fontSize: 11 }}>
          {frames.length} frames
        </span>
        <span style={{ flex: 1 }} />
        {meta.version && (
          <span className="faded" style={{ fontSize: 11 }}>
            v{meta.version}
          </span>
        )}
      </div>

      {mode === 'view' ? (
        <BsdCanvas frames={frames} tokens={tokens} />
      ) : (
        <pre
          style={{
            margin: 0,
            padding: 16,
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
            lineHeight: '18px',
            color: 'var(--gray-200)',
            whiteSpace: 'pre',
            overflow: 'auto',
            flex: 1,
            minHeight: 0,
          }}
        >
          {JSON.stringify(bsd, null, 2)}
        </pre>
      )}
    </div>
  )
}
