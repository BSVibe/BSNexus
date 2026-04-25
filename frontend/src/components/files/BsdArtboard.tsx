import { I } from '../../lib/icons'
import type { BsdFrame, BsdLayer, BsdTokens } from '../../lib/bsd-types'

interface BsdArtboardProps {
  frame: BsdFrame
  tokens: BsdTokens
  w: number
  h: number
}

export default function BsdArtboard({ frame, tokens, w, h }: BsdArtboardProps) {
  const bg = tokens.colors[frame.bg] ?? frame.bg ?? '#0a0b0f'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: w }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 11,
          color: 'var(--text-secondary)',
        }}
      >
        <I.Design size={10} />
        <span style={{ fontWeight: 500, color: 'var(--gray-200)' }}>{frame.name}</span>
        <span className="mono faded" style={{ fontSize: 10 }}>
          {frame.w}×{frame.h}
        </span>
      </div>
      <div
        style={{
          width: w,
          height: h,
          background: bg,
          borderRadius: 'var(--r-md)',
          border: '1px solid var(--border-default)',
          boxShadow: '0 12px 40px rgba(0,0,0,0.4)',
          overflow: 'hidden',
        }}
      >
        <svg
          viewBox={`0 0 ${frame.w} ${frame.h}`}
          width={w}
          height={h}
          preserveAspectRatio="xMidYMid meet"
          style={{ display: 'block' }}
        >
          <BsdLayers layers={frame.layers} tokens={tokens} />
        </svg>
      </div>
    </div>
  )
}

interface LayersProps {
  layers: BsdLayer[]
  tokens: BsdTokens
  ox?: number
  oy?: number
}

function BsdLayers({ layers, tokens, ox = 0, oy = 0 }: LayersProps) {
  const color = (c: string) => tokens.colors[c] ?? c
  const radius = (r: number | string | undefined) => {
    if (typeof r === 'string') return tokens.radius[r] ?? 0
    return r ?? 0
  }

  return (
    <>
      {layers.map((L, i) => {
        if (L.t === 'rect') {
          return (
            <rect
              key={i}
              x={ox + L.x}
              y={oy + L.y}
              width={L.w}
              height={L.h}
              rx={radius(L.r)}
              fill={color(L.fill)}
            />
          )
        }
        if (L.t === 'text') {
          const [fwRaw, fsRaw] = (L.font ?? '400 14px sans').split(' ')
          const size = parseInt(fsRaw, 10) || 14
          const weight = parseInt(fwRaw, 10) || 400
          return (
            <text
              key={i}
              x={ox + L.x}
              y={oy + L.y}
              fontSize={size}
              fontWeight={weight}
              fontFamily="Plus Jakarta Sans, sans-serif"
              textAnchor={L.align === 'center' ? 'middle' : 'start'}
              fill={color(L.fill)}
            >
              {L.text}
            </text>
          )
        }
        if (L.t === 'group') {
          return (
            <g key={i}>
              <BsdLayers
                layers={L.children}
                tokens={tokens}
                ox={ox + L.x}
                oy={oy + L.y}
              />
            </g>
          )
        }
        if (L.t === 'kpi') {
          return (
            <g key={i}>
              <rect
                x={ox + L.x}
                y={oy + L.y}
                width={L.w}
                height={L.h}
                rx={8}
                fill={color('surf')}
              />
              <text
                x={ox + L.x + 16}
                y={oy + L.y + 28}
                fontSize={11}
                fill={color('muted')}
                fontFamily="Plus Jakarta Sans"
              >
                {L.label}
              </text>
              <text
                x={ox + L.x + 16}
                y={oy + L.y + 62}
                fontSize={26}
                fontWeight={700}
                fill={color('text')}
                fontFamily="Plus Jakarta Sans"
              >
                {L.value}
              </text>
              {L.delta && (
                <text
                  x={ox + L.x + 16}
                  y={oy + L.y + 82}
                  fontSize={11}
                  fill={color('accent')}
                  fontFamily="Plus Jakarta Sans"
                >
                  {L.delta}
                </text>
              )}
            </g>
          )
        }
        if (L.t === 'chart') {
          const pts = L.series
          const max = Math.max(...pts)
          const step = L.w / (pts.length - 1)
          const toY = (v: number) => oy + L.y + L.h - (v / max) * L.h * 0.9
          const path = pts
            .map((v, idx) => `${idx === 0 ? 'M' : 'L'} ${ox + L.x + idx * step} ${toY(v)}`)
            .join(' ')
          return (
            <g key={i}>
              {L.kind === 'area' && (
                <path
                  d={`${path} L ${ox + L.x + L.w} ${oy + L.y + L.h} L ${ox + L.x} ${oy + L.y + L.h} Z`}
                  fill={color('accent')}
                  opacity="0.15"
                />
              )}
              <path d={path} stroke={color('accent')} strokeWidth="2" fill="none" />
              {pts.map((v, j) => (
                <circle
                  key={j}
                  cx={ox + L.x + j * step}
                  cy={toY(v)}
                  r={2.5}
                  fill={color('accent')}
                />
              ))}
            </g>
          )
        }
        if (L.t === 'row') {
          return (
            <g key={i}>
              {L.data.map((r, j) => {
                const keys = Object.keys(r)
                return (
                  <g key={j}>
                    <line
                      x1={ox + L.x}
                      x2={ox + L.x + L.w}
                      y1={oy + L.y + j * 28 + 14}
                      y2={oy + L.y + j * 28 + 14}
                      stroke={color('muted')}
                      opacity="0.15"
                    />
                    {keys.map((k, ki) => (
                      <text
                        key={k}
                        x={ox + L.x + ki * (L.w / keys.length)}
                        y={oy + L.y + j * 28 + 10}
                        fontSize={11}
                        fill={color('text')}
                        fontFamily="JetBrains Mono, monospace"
                      >
                        {String(r[k])}
                      </text>
                    ))}
                  </g>
                )
              })}
            </g>
          )
        }
        if (L.t === 'pill') {
          const toneFill: Record<string, string> = {
            amber: 'rgba(245,158,11,0.15)',
            emerald: 'rgba(16,185,129,0.15)',
            rose: 'rgba(244,63,94,0.15)',
            blue: 'rgba(59,130,246,0.15)',
          }
          const toneText: Record<string, string> = {
            amber: '#fcd34d',
            emerald: '#6ee7b7',
            rose: '#fda4af',
            blue: '#93c5fd',
          }
          return (
            <g key={i}>
              <rect
                x={ox + L.x}
                y={oy + L.y}
                width={110}
                height={24}
                rx={12}
                fill={toneFill[L.tone] ?? toneFill.blue}
              />
              <text
                x={ox + L.x + 12}
                y={oy + L.y + 16}
                fontSize={11}
                fontWeight={500}
                fill={toneText[L.tone] ?? toneText.blue}
                fontFamily="Plus Jakarta Sans"
              >
                {L.label}
              </text>
            </g>
          )
        }
        return null
      })}
    </>
  )
}
