// Types for the founder-metaphor surfaces.
// Matching backend models in backend/src/models/.

export type RequestStatus = 'open' | 'running' | 'completed' | 'abandoned'

export interface Request {
  id: string
  tenantId: string
  projectId: string
  originMessageId: string | null
  intentSummary: string
  status: RequestStatus
  userConfirmed: boolean
  supersededById: string | null
  compositionRootId: string | null
  createdAt: string
  updatedAt: string
}

export type DeliverableType = 'code' | 'doc' | 'design' | 'data' | 'url'
export type DeliverableStatus = 'draft' | 'ready' | 'delivered'
export type StorageBackend = 'git' | 'object' | 'url'

export interface Deliverable {
  id: string
  tenantId: string
  projectId: string
  requestId: string | null
  type: DeliverableType
  title: string
  status: DeliverableStatus
  currentVersionId: string | null
  createdAt: string
  updatedAt: string
}

export interface DeliverableVersion {
  id: string
  deliverableId: string
  versionInt: number
  storageBackend: StorageBackend
  contentRef: Record<string, unknown>
  contentHash: string
  sizeBytes: number | null
  createdByRunId: string | null
  createdAt: string
}

export interface Decision {
  id: string
  tenantId: string
  projectId: string
  requestId: string | null
  originRunId: string | null
  question: string
  options: string[]
  blocking: boolean
  resolvedAt: string | null
  resolution: string | null
  resolvedBy: string | null
  createdAt: string
}

export type RunStatus = 'pending' | 'running' | 'blocked' | 'done'
export type CompositionSource = 'bsage' | 'local'

export interface ExecutionRun {
  id: string
  tenantId: string
  projectId: string
  requestId: string
  parentRunId: string | null
  compositionSnapshotId: string | null
  status: RunStatus
  priority: string
  actualCostCents: number
  branchName: string | null
  commitHash: string | null
  errorMessage: string | null
  createdAt: string
  startedAt: string | null
  completedAt: string | null
}

export interface CompositionSnapshot {
  id: string
  tenantId: string
  requestId: string
  executionRunId: string | null
  source: CompositionSource
  bsageCompositionId: string | null
  systemPromptRef: Record<string, unknown>
  toolsAllowed: string[]
  contextDocRefs: Array<{ path: string; title: string; score: number; excerptHash: string }>
  personaLabel: string
  fitScore: number | null
  createdAt: string
}

export type IntegrationProvider = 'bsage' | 'bsgateway' | 'bsupervisor'
export type AuditFailMode = 'open' | 'closed'

export interface IntegrationConfig {
  provider: IntegrationProvider
  enabled: boolean
  baseUrl: string | null
  hasApiKey: boolean
  extraConfig: Record<string, unknown>
}
