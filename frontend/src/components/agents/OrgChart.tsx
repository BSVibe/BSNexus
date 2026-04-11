import { useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { Agent, AgentOrgChartNode } from '../../types/agent'
import { useAgentStore } from '../../stores/agentStore'

import { AGENT_STATUS_COLORS, AGENT_STATUS_FALLBACK_COLOR } from '../../constants/agentStatus'

const EXECUTOR_LABELS: Record<string, string> = {
  claude_code: 'Claude Code',
  claude_api: 'LLM API',
  bsgateway: 'BSGateway',
  generic_llm: 'LLM API',
  worker: 'Worker',
}

/** Custom node component for agent cards */
function AgentNode({ data }: NodeProps) {
  const agent = data.agent as Agent
  const { selectAgent } = useAgentStore()
  const budgetPct =
    agent.monthly_budget_cents && agent.monthly_budget_cents > 0
      ? Math.round((agent.current_month_spent_cents / agent.monthly_budget_cents) * 100)
      : null

  return (
    <>
      <Handle type="target" position={Position.Top} className="!bg-transparent !border-0 !w-0 !h-0" />
      <button
        onClick={() => selectAgent(agent)}
        className="w-52 p-3.5 rounded-xl bg-[#1f1f24] border border-[#424754]/15 hover:border-[#adc6ff]/30 transition-all text-left cursor-pointer shadow-lg shadow-black/20"
      >
        <div className="flex items-center gap-2 mb-1.5">
          <div
            className="w-2.5 h-2.5 rounded-full shrink-0"
            style={{ backgroundColor: AGENT_STATUS_COLORS[agent.status] || AGENT_STATUS_FALLBACK_COLOR }}
          />
          <span className="text-sm font-bold text-[#e3e2e8] truncate">{agent.name}</span>
        </div>
        <p className="text-[11px] text-[#c2c6d6] truncate mb-2">{agent.title || agent.role}</p>
        <div className="flex items-center justify-between">
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#adc6ff]/10 text-[#adc6ff] font-medium">
            {EXECUTOR_LABELS[agent.executor_type] || agent.executor_type}
          </span>
          <span className="text-[10px] text-[#8c909f]">
            ${((agent.current_month_spent_cents || 0) / 100).toFixed(0)}/{agent.monthly_budget_cents != null ? `$${(agent.monthly_budget_cents / 100).toFixed(0)}` : '∞'}
          </span>
        </div>
        {agent.monthly_budget_cents != null && agent.monthly_budget_cents > 0 && (
          <div className="mt-2">
            <div className="h-1 w-full bg-[#343439] rounded-full overflow-hidden">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.min(budgetPct ?? 0, 100)}%`,
                  backgroundColor: (budgetPct ?? 0) >= 90 ? '#ffb4ab' : (budgetPct ?? 0) >= 70 ? '#eab308' : '#adc6ff',
                }}
              />
            </div>
          </div>
        )}
      </button>
      <Handle type="source" position={Position.Bottom} className="!bg-transparent !border-0 !w-0 !h-0" />
    </>
  )
}

const nodeTypes = { agent: AgentNode }

/** Layout constants */
const NODE_WIDTH = 208
const NODE_HEIGHT = 110
const H_GAP = 32
const V_GAP = 80

/** Calculate the width of a subtree (in pixels) */
function subtreeWidth(node: AgentOrgChartNode): number {
  if (node.children.length === 0) return NODE_WIDTH
  const childrenWidth = node.children.reduce((sum, c) => sum + subtreeWidth(c), 0)
  const gaps = (node.children.length - 1) * H_GAP
  return Math.max(NODE_WIDTH, childrenWidth + gaps)
}

/** Recursively layout nodes and collect edges */
function layoutTree(
  node: AgentOrgChartNode,
  x: number,
  y: number,
  nodes: Node[],
  edges: Edge[],
) {
  const nodeId = node.agent.id

  nodes.push({
    id: nodeId,
    type: 'agent',
    position: { x: x - NODE_WIDTH / 2, y },
    data: { agent: node.agent },
    draggable: false,
  })

  if (node.children.length === 0) return

  const totalWidth = node.children.reduce((sum, c) => sum + subtreeWidth(c), 0) + (node.children.length - 1) * H_GAP
  let childX = x - totalWidth / 2

  for (const child of node.children) {
    const w = subtreeWidth(child)
    const childCenterX = childX + w / 2
    const childY = y + NODE_HEIGHT + V_GAP

    edges.push({
      id: `${nodeId}-${child.agent.id}`,
      source: nodeId,
      target: child.agent.id,
      type: 'smoothstep',
      style: { stroke: '#424754', strokeWidth: 1.5 },
      animated: false,
    })

    layoutTree(child, childCenterX, childY, nodes, edges)
    childX += w + H_GAP
  }
}

interface OrgChartProps {
  orgChart: AgentOrgChartNode[]
}

export default function OrgChart({ orgChart }: OrgChartProps) {
  const { nodes, edges } = useMemo(() => {
    const n: Node[] = []
    const e: Edge[] = []

    // Multiple root nodes side by side
    const rootWidths = orgChart.map(subtreeWidth)
    const totalWidth = rootWidths.reduce((s, w) => s + w, 0) + (orgChart.length - 1) * H_GAP * 2
    let rootX = -totalWidth / 2

    for (let i = 0; i < orgChart.length; i++) {
      const w = rootWidths[i]
      const centerX = rootX + w / 2
      layoutTree(orgChart[i], centerX, 0, n, e)
      rootX += w + H_GAP * 2
    }

    return { nodes: n, edges: e }
  }, [orgChart])

  const defaultViewport = useMemo(() => {
    if (nodes.length === 0) return { x: 0, y: 40, zoom: 1 }
    // Auto-fit: calculate bounds
    let minX = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const n of nodes) {
      minX = Math.min(minX, n.position.x)
      maxX = Math.max(maxX, n.position.x + NODE_WIDTH)
      maxY = Math.max(maxY, n.position.y + NODE_HEIGHT)
    }
    const width = maxX - minX
    const centerX = (minX + maxX) / 2
    // Scale to fit ~900px viewport width, cap at 1
    const zoom = Math.min(1, 800 / Math.max(width, 400))
    return { x: -centerX * zoom + 450, y: 40, zoom }
  }, [nodes])

  return (
    <div className="w-full flex-1 bg-stitch-surface rounded-xl border border-stitch-outline-variant/10">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        defaultViewport={defaultViewport}
        fitView
        fitViewOptions={{ padding: 0.3, maxZoom: 1.2 }}
        minZoom={0.2}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        panOnScroll
        zoomOnScroll
      >
        <Background color="#343439" gap={32} size={1} />
        <Controls
          className="!bg-[#1a1b20] !border-[#424754]/30 !shadow-lg [&>button]:!bg-[#1a1b20] [&>button]:!border-[#424754]/30 [&>button]:!text-[#c2c6d6] [&>button:hover]:!bg-[#292a2e]"
        />
      </ReactFlow>
    </div>
  )
}
