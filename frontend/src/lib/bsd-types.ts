/**
 * .bsd (BSVibe Design) spec types.
 *
 * `.bsd` files are JSON documents that describe one or more design
 * frames as a flat list of primitive layers (rect/text/group/kpi/chart/
 * row/pill). The format is intentionally small so the `BsdCanvas`
 * viewer can render any frame as an SVG artboard without pulling in a
 * heavyweight design toolkit.
 */

export interface BsdTokens {
  colors: Record<string, string>
  radius: Record<string, number>
}

export interface BsdMeta {
  name: string
  version?: string
  fps?: number
}

export type BsdLayer =
  | BsdRectLayer
  | BsdTextLayer
  | BsdGroupLayer
  | BsdKpiLayer
  | BsdChartLayer
  | BsdRowLayer
  | BsdPillLayer

export interface BsdRectLayer {
  t: 'rect'
  x: number
  y: number
  w: number
  h: number
  fill: string
  r?: number | string
}

export interface BsdTextLayer {
  t: 'text'
  x: number
  y: number
  text: string
  font?: string
  fill: string
  align?: 'start' | 'center'
}

export interface BsdGroupLayer {
  t: 'group'
  name?: string
  x: number
  y: number
  children: BsdLayer[]
}

export interface BsdKpiLayer {
  t: 'kpi'
  x: number
  y: number
  w: number
  h: number
  label: string
  value: string
  delta?: string
}

export interface BsdChartLayer {
  t: 'chart'
  x: number
  y: number
  w: number
  h: number
  kind: 'line' | 'area'
  series: number[]
}

export interface BsdRowLayer {
  t: 'row'
  x: number
  y: number
  w: number
  data: Record<string, string | number>[]
}

export interface BsdPillLayer {
  t: 'pill'
  x: number
  y: number
  label: string
  tone: 'amber' | 'emerald' | 'rose' | 'blue'
}

export interface BsdFrame {
  id: string
  name: string
  w: number
  h: number
  bg: string
  layers: BsdLayer[]
}

export interface BsdDocument {
  meta: BsdMeta
  tokens: BsdTokens
  frames: BsdFrame[]
}

export type FileType = 'bsd' | 'md' | 'code' | 'data' | 'url'

export interface ProjectFile {
  path: string
  type: FileType
  size?: string
  lang?: string
  content?: string
  bsd?: BsdDocument
}
