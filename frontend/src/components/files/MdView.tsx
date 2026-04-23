import { useMemo } from 'react'

/**
 * Intentionally simple markdown renderer — handles the subset we ship
 * inside sample .md files (headings, bold, inline code, lists, tables).
 * No dependency on a full markdown package.
 */
export default function MdView({ content }: { content: string }) {
  const html = useMemo(() => renderMarkdown(content), [content])
  return (
    <div
      style={{
        padding: '24px 32px',
        maxWidth: 780,
        margin: '0 auto',
        color: 'var(--gray-200)',
        fontSize: 14,
        lineHeight: '22px',
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}

function renderMarkdown(src: string): string {
  let out = src

  // Tables: stop at first blank line.
  out = out.replace(/((?:^\|.*\|\n)+)/gm, (block) => {
    const rows = block
      .trim()
      .split('\n')
      .map((r) => r.slice(1, -1).split('|').map((s) => s.trim()))
    const [head, _sep, ...body] = rows
    return `<table style="width:100%;border-collapse:collapse;margin:12px 0">
<thead><tr>${head.map((h) => `<th style="padding:6px 8px;border-bottom:1px solid var(--border-subtle);text-align:left">${h}</th>`).join('')}</tr></thead>
<tbody>${body
      .map(
        (r) =>
          `<tr>${r.map((c) => `<td style="padding:6px 8px;border-bottom:1px solid var(--border-subtle)">${c}</td>`).join('')}</tr>`,
      )
      .join('')}</tbody>
</table>`
  })

  out = out
    .replace(/^### (.*)$/gm, '<h3>$1</h3>')
    .replace(/^## (.*)$/gm, '<h2>$1</h2>')
    .replace(/^# (.*)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(
      /`([^`]+)`/g,
      '<code style="background:var(--bg-elevated);padding:1px 6px;border-radius:var(--r-sm);font-size:12px">$1</code>',
    )
    .replace(/^\- (.+)$/gm, '<li>$1</li>')

  return out.replace(/\n\n/g, '<p></p>')
}
