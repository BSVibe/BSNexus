/**
 * ScreenRenderer — renders a .bsd `spec` AST as live HTML.
 *
 * The Designer agent (Qwen3) emits spec trees in several shapes:
 *  - Flat props: `{type, text, title, icon, variant, style, children}`
 *  - RN-style props:  `{type, props: {style, ...}, children}`
 *  - Domain types: `Screen`, `Appbar`, `Container`, `Fab`, `TodoList`,
 *    `TodoItem`, `ColorCard`, `Button`, `TextInput`, etc.
 *
 * The renderer accepts both shapes (flat props win when no `props` key)
 * and provides sensible visual fallbacks for domain types so every
 * screen looks like something instead of a JSON dump.
 */
import type { CSSProperties, ReactNode } from 'react'

/* ------------------------------------------------------------------ */
/* Types                                                                */
/* ------------------------------------------------------------------ */

export interface SpecNode {
  type?: string
  props?: Record<string, unknown>
  // Flat-style top-level attrs also supported — anything not in this
  // list is treated as a prop.
  children?: SpecNode[] | string | number | null
  style?: Record<string, unknown> | Array<Record<string, unknown>>
  data?: unknown[]
  renderItem?: SpecNode | null
  [key: string]: unknown
}

export interface DesignTokens {
  colors?: Record<string, unknown>
  spacing?: Record<string, unknown>
  typography?: Record<string, unknown>
  radii?: Record<string, unknown>
  [key: string]: unknown
}

interface RenderContext {
  tokens: DesignTokens
  depth: number
}

/* ------------------------------------------------------------------ */
/* Attribute extraction — handles both flat and RN prop shapes          */
/* ------------------------------------------------------------------ */

const STRUCTURAL_KEYS = new Set(['type', 'children', 'props'])

function getAttrs(node: SpecNode): Record<string, unknown> {
  const flat: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(node)) {
    if (!STRUCTURAL_KEYS.has(k)) flat[k] = v
  }
  const nested = (node.props ?? {}) as Record<string, unknown>
  return { ...flat, ...nested } // nested props override flat
}

function getStyle(attrs: Record<string, unknown>): Record<string, unknown> | undefined {
  const s = attrs.style
  if (s && (typeof s === 'object' || Array.isArray(s))) {
    return s as Record<string, unknown> | Record<string, unknown>
  }
  return undefined
}

/* ------------------------------------------------------------------ */
/* Style translation                                                    */
/* ------------------------------------------------------------------ */

const CSS_KEY_MAP: Record<string, string> = {
  paddingHorizontal: 'paddingInline',
  paddingVertical: 'paddingBlock',
  marginHorizontal: 'marginInline',
  marginVertical: 'marginBlock',
  textDecorationLine: 'textDecoration',
  shadowColor: 'boxShadow',
}

const PX_KEYS = new Set([
  'padding', 'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight',
  'paddingInline', 'paddingBlock',
  'margin', 'marginTop', 'marginBottom', 'marginLeft', 'marginRight',
  'marginInline', 'marginBlock',
  'borderRadius', 'borderWidth', 'borderBottomWidth', 'borderTopWidth',
  'borderLeftWidth', 'borderRightWidth',
  'width', 'height', 'minWidth', 'minHeight', 'maxWidth', 'maxHeight',
  'fontSize', 'lineHeight', 'letterSpacing',
  'top', 'right', 'bottom', 'left', 'gap', 'rowGap', 'columnGap',
])

function resolveToken(value: unknown, tokens: DesignTokens): unknown {
  if (typeof value !== 'string' || !value.includes('.')) return value
  const parts = value.split('.')
  let node: unknown = tokens
  for (const part of parts) {
    if (node && typeof node === 'object' && part in node) {
      node = (node as Record<string, unknown>)[part]
    } else {
      return value
    }
  }
  return node ?? value
}

function toCss(
  style: Record<string, unknown> | Array<Record<string, unknown>> | undefined,
  tokens: DesignTokens,
): CSSProperties {
  if (!style) return {}
  const flat: Record<string, unknown> = Array.isArray(style)
    ? Object.assign({}, ...style.filter((s) => s && typeof s === 'object'))
    : style

  const out: Record<string, unknown> = {}
  for (const [rawKey, rawValue] of Object.entries(flat)) {
    const key = CSS_KEY_MAP[rawKey] ?? rawKey
    let value = resolveToken(rawValue, tokens)
    if (typeof value === 'number' && PX_KEYS.has(key)) value = `${value}px`
    out[key] = value
  }
  return out as CSSProperties
}

/* ------------------------------------------------------------------ */
/* Children normalisation                                               */
/* ------------------------------------------------------------------ */

function getChildren(node: SpecNode): Array<SpecNode | string | number> {
  const c = node.children
  if (c == null) return []
  if (Array.isArray(c)) return c.filter((x) => x != null) as Array<SpecNode | string | number>
  if (typeof c === 'string' || typeof c === 'number') return [c]
  return []
}

/* ------------------------------------------------------------------ */
/* Domain type aliases                                                  */
/* ------------------------------------------------------------------ */

/** Map LLM-emitted types to their closest primitive + default visual style. */
type PrimitiveKind =
  | 'view'           // generic block container, flex column
  | 'row'            // flex row container
  | 'text'           // inline text
  | 'heading'        // large text
  | 'button'
  | 'input'
  | 'image'
  | 'list'
  | 'listItem'
  | 'checkbox'
  | 'screen'
  | 'appbar'
  | 'fab'
  | 'card'
  | 'unknown'

function classify(type: string | undefined): PrimitiveKind {
  if (!type) return 'view'
  const t = type.toLowerCase()

  if (['view', 'safeareaview', 'safeview', 'container', 'box', 'stack', 'section', 'column'].includes(t)) return 'view'
  if (['row', 'hstack', 'horizontalstack'].includes(t)) return 'row'
  if (['scrollview', 'scroll'].includes(t)) return 'view'

  if (['text', 'label', 'paragraph', 'caption'].includes(t)) return 'text'
  if (['heading', 'title', 'h1', 'h2', 'h3', 'headline'].includes(t)) return 'heading'

  if (['button', 'touchableopacity', 'pressable', 'iconbutton'].includes(t)) return 'button'
  if (['textinput', 'input', 'searchinput', 'textfield'].includes(t)) return 'input'
  if (['image', 'avatar', 'thumbnail'].includes(t)) return 'image'

  if (['flatlist', 'sectionlist', 'list', 'todolist', 'itemlist'].includes(t)) return 'list'
  if (['listitem', 'todoitem', 'row-item', 'card-item'].includes(t)) return 'listItem'

  if (['switch', 'checkbox', 'toggle'].includes(t)) return 'checkbox'

  if (['screen', 'page', 'view-root'].includes(t)) return 'screen'
  if (['appbar', 'navbar', 'header', 'topbar', 'titlebar'].includes(t)) return 'appbar'
  if (['fab', 'floatingbutton', 'floatingactionbutton'].includes(t)) return 'fab'
  if (['card', 'colorcard', 'tile', 'chip'].includes(t)) return 'card'

  return 'unknown'
}

/* ------------------------------------------------------------------ */
/* Renderer                                                             */
/* ------------------------------------------------------------------ */

function renderNode(
  node: SpecNode | string | number | null | undefined,
  ctx: RenderContext,
  key: string,
): ReactNode {
  if (node == null) return null
  if (typeof node === 'string' || typeof node === 'number') return <span key={key}>{node}</span>

  const type = node.type || 'View'
  const attrs = getAttrs(node)
  const style = toCss(getStyle(attrs), ctx.tokens)
  const kind = classify(type)

  const children = getChildren(node).map((c, i) =>
    renderNode(c as SpecNode, { ...ctx, depth: ctx.depth + 1 }, `${key}-${i}`),
  )

  const textContent =
    typeof attrs.text === 'string' ? (attrs.text as string)
    : typeof attrs.title === 'string' ? (attrs.title as string)
    : typeof attrs.label === 'string' ? (attrs.label as string)
    : typeof attrs.value === 'string' ? (attrs.value as string)
    : null

  switch (kind) {
    case 'screen':
      return (
        <div
          key={key}
          style={{
            display: 'flex',
            flexDirection: 'column',
            minHeight: '100%',
            background: '#ffffff',
            ...style,
          }}
          data-rn-type={type}
        >
          {children}
        </div>
      )

    case 'appbar': {
      const t = typeof attrs.title === 'string' ? (attrs.title as string) : null
      return (
        <div
          key={key}
          style={{
            display: 'flex',
            alignItems: 'center',
            padding: '14px 16px',
            background: '#1f2937',
            color: '#ffffff',
            fontSize: 16,
            fontWeight: 600,
            boxShadow: '0 1px 0 rgba(0,0,0,0.06)',
            ...style,
          }}
          data-rn-type={type}
        >
          {t ?? children}
        </div>
      )
    }

    case 'view':
      return (
        <div
          key={key}
          style={{
            display: 'flex',
            flexDirection: (style.flexDirection as CSSProperties['flexDirection']) ?? 'column',
            boxSizing: 'border-box',
            padding: style.padding != null ? style.padding : children.length ? 12 : undefined,
            gap: (style.gap as CSSProperties['gap']) ?? 8,
            ...style,
          }}
          data-rn-type={type}
        >
          {children}
        </div>
      )

    case 'row':
      return (
        <div
          key={key}
          style={{
            display: 'flex',
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            ...style,
          }}
          data-rn-type={type}
        >
          {children}
        </div>
      )

    case 'text':
      return (
        <span key={key} style={{ color: '#111827', fontSize: 14, lineHeight: 1.4, ...style }} data-rn-type={type}>
          {textContent ?? (children.length ? children : '')}
        </span>
      )

    case 'heading':
      return (
        <h2 key={key} style={{ margin: 0, fontSize: 20, fontWeight: 700, color: '#111827', ...style }} data-rn-type={type}>
          {textContent ?? (children.length ? children : '')}
        </h2>
      )

    case 'button': {
      const variant = String(attrs.variant ?? 'primary').toLowerCase()
      const variantStyle: CSSProperties =
        variant === 'secondary'
          ? { background: '#f3f4f6', color: '#111827', border: '1px solid #e5e7eb' }
        : variant === 'danger'
          ? { background: '#ef4444', color: '#ffffff' }
        : variant === 'ghost'
          ? { background: 'transparent', color: '#2563eb', border: '1px solid #bfdbfe' }
          : { background: '#2563eb', color: '#ffffff' }
      return (
        <button
          key={key}
          type="button"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '10px 16px',
            borderRadius: 8,
            fontSize: 14,
            fontWeight: 600,
            cursor: 'pointer',
            border: 'none',
            ...variantStyle,
            ...style,
          }}
          data-rn-type={type}
        >
          {textContent ?? (children.length ? children : 'Button')}
        </button>
      )
    }

    case 'input': {
      const multiline = Boolean(attrs.multiline)
      const placeholder = typeof attrs.placeholder === 'string' ? (attrs.placeholder as string) : ''
      const label = typeof attrs.label === 'string' ? (attrs.label as string) : null
      const defaultValue = typeof attrs.value === 'string'
        ? (attrs.value as string)
        : typeof attrs.defaultValue === 'string' ? (attrs.defaultValue as string) : ''
      const field = multiline ? (
        <textarea
          placeholder={placeholder}
          defaultValue={defaultValue}
          rows={3}
          style={{
            width: '100%',
            border: '1px solid #d1d5db',
            borderRadius: 6,
            padding: '8px 10px',
            fontSize: 14,
            fontFamily: 'inherit',
            boxSizing: 'border-box',
            ...style,
          }}
          data-rn-type={type}
        />
      ) : (
        <input
          type={String(attrs.secureTextEntry) === 'true' ? 'password' : 'text'}
          placeholder={placeholder}
          defaultValue={defaultValue}
          style={{
            width: '100%',
            border: '1px solid #d1d5db',
            borderRadius: 6,
            padding: '8px 10px',
            fontSize: 14,
            fontFamily: 'inherit',
            boxSizing: 'border-box',
            ...style,
          }}
          data-rn-type={type}
        />
      )
      return (
        <div key={key} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {label != null && (
            <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>{label}</span>
          )}
          {field}
        </div>
      )
    }

    case 'image': {
      const src = (attrs.source as { uri?: string } | string | undefined) ?? (attrs.src as string | undefined)
      const uri = typeof src === 'string' ? src : src?.uri
      return (
        <div key={key} style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: 80,
          background: '#f3f4f6',
          borderRadius: 6,
          color: '#9ca3af',
          fontSize: 11,
          ...style,
        }} data-rn-type={type}>
          {uri ? <img src={uri} alt="" style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'cover' }} /> : '[image]'}
        </div>
      )
    }

    case 'list': {
      const items: unknown[] = Array.isArray(attrs.data) ? (attrs.data as unknown[]) : []
      const renderTemplate = (node.renderItem ?? (attrs.renderItem as SpecNode | undefined)) ?? null
      if (children.length > 0 || items.length === 0) {
        return (
          <div key={key} style={{ display: 'flex', flexDirection: 'column', gap: 1, background: '#f3f4f6', borderRadius: 8, overflow: 'hidden', ...style }} data-rn-type={type}>
            {children.length ? children : (
              <div style={{ padding: 14, color: '#9ca3af', fontSize: 12, fontStyle: 'italic' }}>Empty {type}</div>
            )}
          </div>
        )
      }
      return (
        <div key={key} style={{ display: 'flex', flexDirection: 'column', gap: 1, background: '#f3f4f6', borderRadius: 8, overflow: 'hidden', ...style }} data-rn-type={type}>
          {items.map((item, i) => {
            const itemKey = `${key}-item-${i}`
            if (renderTemplate) return <div key={itemKey}>{renderNode(renderTemplate, { ...ctx, depth: ctx.depth + 1 }, itemKey)}</div>
            return (
              <div key={itemKey} style={{ padding: 10, background: '#ffffff' }}>
                {typeof item === 'object' ? JSON.stringify(item) : String(item)}
              </div>
            )
          })}
        </div>
      )
    }

    case 'listItem': {
      const completed = Boolean(attrs.completed)
      const title = typeof attrs.text === 'string' ? attrs.text
        : typeof attrs.title === 'string' ? attrs.title : null
      const desc = typeof attrs.description === 'string' ? (attrs.description as string) : null
      return (
        <div key={key} style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 10,
          padding: '12px 14px',
          background: '#ffffff',
          borderBottom: '1px solid #f3f4f6',
          ...style,
        }} data-rn-type={type}>
          <input type="checkbox" defaultChecked={completed} style={{ marginTop: 3 }} />
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
            {title != null && (
              <span style={{ fontSize: 14, color: '#111827', textDecoration: completed ? 'line-through' : undefined, opacity: completed ? 0.6 : 1 }}>{title}</span>
            )}
            {desc != null && <span style={{ fontSize: 12, color: '#6b7280' }}>{desc}</span>}
            {children.length > 0 && <div>{children}</div>}
          </div>
        </div>
      )
    }

    case 'checkbox': {
      const checked = Boolean(attrs.value ?? attrs.defaultValue ?? attrs.checked)
      return <input key={key} type="checkbox" defaultChecked={checked} style={style} data-rn-type={type} />
    }

    case 'fab': {
      const icon = typeof attrs.icon === 'string' ? (attrs.icon as string) : '＋'
      return (
        <div
          key={key}
          style={{
            position: 'absolute',
            right: 20,
            bottom: 20,
            width: 56,
            height: 56,
            borderRadius: 28,
            background: '#2563eb',
            color: '#ffffff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 22,
            fontWeight: 700,
            boxShadow: '0 6px 16px rgba(37,99,235,0.35)',
            ...style,
          }}
          data-rn-type={type}
        >
          {icon.length > 2 ? icon[0]?.toUpperCase() : icon}
        </div>
      )
    }

    case 'card': {
      // ColorCard-style: color swatch with label/hex
      const color = typeof attrs.color === 'string' ? (attrs.color as string) : null
      const label = typeof attrs.label === 'string' ? (attrs.label as string) : null
      const hex = typeof attrs.hex === 'string' ? (attrs.hex as string) : null
      if (color) {
        return (
          <div key={key} style={{
            width: 96,
            display: 'flex',
            flexDirection: 'column',
            borderRadius: 8,
            overflow: 'hidden',
            border: '1px solid #e5e7eb',
            ...style,
          }} data-rn-type={type}>
            <div style={{ height: 56, background: color }} />
            <div style={{ padding: '6px 8px', fontSize: 11, color: '#111827' }}>
              {label && <div style={{ fontWeight: 600 }}>{label}</div>}
              {hex && <div style={{ color: '#6b7280' }}>{hex}</div>}
            </div>
          </div>
        )
      }
      // Generic card
      return (
        <div key={key} style={{ padding: 12, borderRadius: 8, background: '#ffffff', border: '1px solid #e5e7eb', ...style }} data-rn-type={type}>
          {textContent ?? (children.length ? children : null)}
        </div>
      )
    }

    case 'unknown':
    default:
      return (
        <div
          key={key}
          style={{
            padding: 8,
            border: '1px dashed rgba(250,204,21,0.5)',
            borderRadius: 4,
            background: 'rgba(250,204,21,0.05)',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
            ...style,
          }}
          data-rn-type={type}
        >
          <span style={{ fontSize: 10, color: '#b45309', fontWeight: 600, textTransform: 'uppercase' }}>
            {type}
          </span>
          {textContent != null && <span style={{ fontSize: 13, color: '#111827' }}>{textContent}</span>}
          {children.length > 0 && <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>{children}</div>}
        </div>
      )
  }
}

/* ------------------------------------------------------------------ */
/* Public component                                                     */
/* ------------------------------------------------------------------ */

export interface ScreenRendererProps {
  spec: SpecNode | Record<string, unknown> | null
  tokens?: DesignTokens
  width?: number
  height?: number
}

export default function ScreenRenderer({ spec, tokens, width = 390, height = 844 }: ScreenRendererProps) {
  if (!spec || typeof spec !== 'object') {
    return (
      <div className="text-xs text-text-tertiary italic px-3 py-4">
        Empty spec — nothing to render.
      </div>
    )
  }

  const ctx: RenderContext = { tokens: tokens ?? {}, depth: 0 }

  return (
    <div className="flex items-start justify-center py-4">
      <div
        style={{
          width,
          height,
          background: '#ffffff',
          color: '#111827',
          borderRadius: 24,
          overflow: 'auto',
          position: 'relative',
          boxShadow: '0 10px 30px rgba(0,0,0,0.35)',
          border: '1px solid rgba(255,255,255,0.08)',
          display: 'flex',
          flexDirection: 'column',
        }}
        data-design-frame="true"
      >
        {renderNode(spec as SpecNode, ctx, 'root')}
      </div>
    </div>
  )
}
