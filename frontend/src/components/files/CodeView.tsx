import { Fragment } from 'react'

export default function CodeView({ content, lang }: { content: string; lang: string }) {
  const lines = content.split('\n')
  return (
    <div
      style={{
        padding: '16px 0',
        fontFamily: 'var(--font-mono)',
        fontSize: 12,
        lineHeight: '20px',
        minHeight: '100%',
      }}
    >
      <div
        style={{
          padding: '4px 16px',
          fontSize: 10,
          color: 'var(--text-tertiary)',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
        }}
      >
        {lang}
      </div>
      <div
        style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 12 }}
      >
        {lines.map((line, i) => (
          <Fragment key={i}>
            <div
              style={{
                textAlign: 'right',
                color: 'var(--text-disabled)',
                paddingLeft: 16,
                userSelect: 'none',
              }}
            >
              {i + 1}
            </div>
            <div
              style={{
                color: 'var(--gray-200)',
                whiteSpace: 'pre',
                paddingRight: 16,
              }}
            >
              {line}
            </div>
          </Fragment>
        ))}
      </div>
    </div>
  )
}
