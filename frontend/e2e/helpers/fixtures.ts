/**
 * Shared mock data fixtures for E2E tests.
 * Mirrors backend API response shapes exactly.
 */

export const mockUser = {
  id: 'user-001',
  email: 'dev@bsvibe.dev',
}

export const mockTokens = {
  access_token: 'mock-access-token-abc123',
  refresh_token: 'mock-refresh-token-def456',
}

export const mockProjects = [
  {
    id: 'proj-001',
    name: 'BSNexus',
    description: 'AI-powered development management system',
    design_doc_path: null,
    repo_path: '/home/dev/bsnexus',
    status: 'active',
    llm_config: null,
    created_at: '2026-03-01T00:00:00Z',
    updated_at: '2026-03-28T12:00:00Z',
    phases: [
      {
        id: 'phase-001',
        project_id: 'proj-001',
        name: 'Core Backend',
        description: 'API and database layer',
        branch_name: 'feat/core-backend',
        order: 0,
        status: 'completed',
        created_at: '2026-03-01T00:00:00Z',
        updated_at: '2026-03-15T00:00:00Z',
      },
      {
        id: 'phase-002',
        project_id: 'proj-001',
        name: 'Frontend',
        description: 'React dashboard',
        branch_name: 'feat/frontend',
        order: 1,
        status: 'active',
        created_at: '2026-03-15T00:00:00Z',
        updated_at: '2026-03-28T00:00:00Z',
      },
    ],
  },
  {
    id: 'proj-002',
    name: 'BSVibe Auth',
    description: 'Centralized authentication service',
    design_doc_path: null,
    repo_path: '/home/dev/bsvibe-auth',
    status: 'completed',
    llm_config: null,
    created_at: '2026-02-01T00:00:00Z',
    updated_at: '2026-03-20T00:00:00Z',
    phases: [
      {
        id: 'phase-003',
        project_id: 'proj-002',
        name: 'OAuth Flow',
        description: 'OAuth2 implementation',
        branch_name: 'feat/oauth',
        order: 0,
        status: 'completed',
        created_at: '2026-02-01T00:00:00Z',
        updated_at: '2026-03-20T00:00:00Z',
      },
    ],
  },
  {
    id: 'proj-003',
    name: 'Worker Agent',
    description: 'Distributed code execution worker',
    design_doc_path: null,
    repo_path: '/home/dev/worker-agent',
    status: 'design',
    llm_config: null,
    created_at: '2026-03-25T00:00:00Z',
    updated_at: '2026-03-28T00:00:00Z',
    phases: [],
  },
]

export const mockProjectsSummary = [
  {
    id: 'proj-001',
    name: 'BSNexus',
    status: 'active',
    task_counts: { waiting: 2, ready: 3, in_progress: 1, review: 1, done: 5 },
    bug_count: 2,
    current_phase: 'Frontend',
    has_architect_session: true,
    last_activity: '2026-03-28T12:00:00Z',
  },
  {
    id: 'proj-002',
    name: 'BSVibe Auth',
    status: 'completed',
    task_counts: { waiting: 0, ready: 0, in_progress: 0, review: 0, done: 8 },
    bug_count: 0,
    current_phase: null,
    has_architect_session: false,
    last_activity: '2026-03-20T00:00:00Z',
  },
  {
    id: 'proj-003',
    name: 'Worker Agent',
    status: 'design',
    task_counts: {},
    bug_count: 0,
    current_phase: null,
    has_architect_session: false,
    last_activity: null,
  },
]

export function makeMockTask(overrides: Record<string, unknown> = {}) {
  return {
    id: 'task-001',
    project_id: 'proj-001',
    phase_id: 'phase-002',
    title: 'Implement dashboard stat cards',
    description: 'Add StatCard component with bento grid layout',
    status: 'ready',
    priority: 'medium',
    task_type: 'feature',
    source: 'architect',
    parent_task_id: null,
    worker_prompt: null,
    qa_prompt: null,
    branch_name: null,
    commit_hash: null,
    qa_result: null,
    output_path: null,
    error_message: null,
    retry_count: 0,
    max_retries: 3,
    qa_feedback_history: null,
    version: 1,
    created_at: '2026-03-28T00:00:00Z',
    updated_at: '2026-03-28T00:00:00Z',
    started_at: null,
    completed_at: null,
    depends_on: [],
    ...overrides,
  }
}

export const mockBoardResponse = {
  project_id: 'proj-001',
  columns: {
    waiting: {
      tasks: [
        makeMockTask({ id: 'task-w1', title: 'Design settings page', status: 'waiting', priority: 'low', task_type: 'feature' }),
        makeMockTask({ id: 'task-w2', title: 'Add notification system', status: 'waiting', priority: 'medium', task_type: 'feature' }),
      ],
    },
    ready: {
      tasks: [
        makeMockTask({ id: 'task-r1', title: 'Implement dashboard stat cards', status: 'ready', priority: 'medium', task_type: 'feature' }),
        makeMockTask({ id: 'task-r2', title: 'Fix auth redirect loop', status: 'ready', priority: 'high', task_type: 'bug' }),
        makeMockTask({ id: 'task-r3', title: 'Add task filtering', status: 'ready', priority: 'low', task_type: 'improvement' }),
      ],
    },
    in_progress: {
      tasks: [
        makeMockTask({ id: 'task-ip1', title: 'Build kanban board', status: 'in_progress', priority: 'high', task_type: 'feature', started_at: '2026-03-28T10:00:00Z' }),
      ],
    },
    review: {
      tasks: [
        makeMockTask({ id: 'task-rv1', title: 'Refactor API client', status: 'review', priority: 'medium', task_type: 'refactor' }),
      ],
    },
    done: {
      tasks: [
        makeMockTask({ id: 'task-d1', title: 'Setup project structure', status: 'done', priority: 'high', task_type: 'chore', completed_at: '2026-03-15T00:00:00Z' }),
        makeMockTask({ id: 'task-d2', title: 'Create database models', status: 'done', priority: 'high', task_type: 'feature', completed_at: '2026-03-18T00:00:00Z' }),
        makeMockTask({ id: 'task-d3', title: 'Write unit tests', status: 'done', priority: 'medium', task_type: 'test', completed_at: '2026-03-20T00:00:00Z' }),
      ],
    },
  },
  stats: { waiting: 2, ready: 3, in_progress: 1, review: 1, done: 3 },
  phases: {
    'phase-001': { name: 'Core Backend', order: 0, status: 'completed' },
    'phase-002': { name: 'Frontend', order: 1, status: 'active' },
  },
  redesign_tasks: [],
}

export const mockSessions = [
  {
    id: 'session-001',
    project_id: 'proj-001',
    name: 'BSNexus design session',
    status: 'project_bound',
    created_at: '2026-03-01T00:00:00Z',
    updated_at: '2026-03-28T00:00:00Z',
    messages: [
      {
        id: 'msg-001',
        session_id: 'session-001',
        role: 'user',
        content: 'I want to build an AI-powered development management system',
        created_at: '2026-03-01T00:00:00Z',
      },
      {
        id: 'msg-002',
        session_id: 'session-001',
        role: 'assistant',
        content: 'I will design a system with the following components: FastAPI backend, React frontend, Redis Streams for queuing, and distributed worker nodes.',
        created_at: '2026-03-01T00:01:00Z',
      },
    ],
  },
  {
    id: 'session-002',
    project_id: null,
    name: 'Exploring new ideas',
    status: 'active',
    created_at: '2026-03-25T00:00:00Z',
    updated_at: '2026-03-25T00:00:00Z',
    messages: [],
  },
]
