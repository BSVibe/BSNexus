/**
 * Shared mock data fixtures for E2E tests.
 * Mirrors backend API response shapes exactly.
 */

export const mockUser = {
  id: 'user-001',
  email: 'dev@bsvibe.dev',
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
    
    last_activity: '2026-03-28T12:00:00Z',
  },
  {
    id: 'proj-002',
    name: 'BSVibe Auth',
    status: 'completed',
    task_counts: { waiting: 0, ready: 0, in_progress: 0, review: 0, done: 8 },
    bug_count: 0,
    current_phase: null,
    
    last_activity: '2026-03-20T00:00:00Z',
  },
  {
    id: 'proj-003',
    name: 'Worker Agent',
    status: 'design',
    task_counts: {},
    bug_count: 0,
    current_phase: null,
    
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
    status: 'pending',
    priority: 'medium',
    task_type: 'feature',
    source: 'llm',
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

export const mockPlanTreeResponse = {
  project_id: 'proj-001',
  project_name: 'BSVibe Tax SaaS',
  project_status: 'active',
  goal: 'Ship MVP by Q3',
  phases: [
    {
      id: 'phase-001',
      name: 'Core Backend',
      description: 'API + DB foundations',
      status: 'completed',
      order: 1,
      tasks: [
        {
          id: 'task-d1',
          title: 'Setup project structure',
          status: 'done',
          priority: 'high',
          task_type: 'chore',
          agent_id: 'agent-001',
          agent_name: 'Alex',
          depends_on_ids: [],
          started_at: '2026-03-15T00:00:00Z',
          completed_at: '2026-03-15T08:00:00Z',
        },
      ],
    },
    {
      id: 'phase-002',
      name: 'Frontend',
      description: 'React UI on top of the API',
      status: 'active',
      order: 2,
      tasks: [
        {
          id: 'task-r1',
          title: 'Implement plan view',
          status: 'pending',
          priority: 'medium',
          task_type: 'feature',
          agent_id: null,
          agent_name: null,
          depends_on_ids: [],
          started_at: null,
          completed_at: null,
        },
        {
          id: 'task-ip1',
          title: 'Build agent status bar',
          status: 'running',
          priority: 'high',
          task_type: 'feature',
          agent_id: 'agent-002',
          agent_name: 'Dev-1',
          depends_on_ids: [],
          started_at: '2026-03-28T10:00:00Z',
          completed_at: null,
        },
      ],
    },
  ],
}

export const mockAgentStatusCards = [
  {
    agent_id: 'agent-001',
    name: 'Alex',
    role: 'cto',
    title: 'CTO',
    dot: 'yellow',
    current_task: null,
  },
  {
    agent_id: 'agent-002',
    name: 'Dev-1',
    role: 'engineer',
    title: 'Senior Engineer',
    dot: 'green',
    current_task: { id: 'task-ip1', title: 'Build agent status bar', status: 'running' },
    activity: '',
  },
]

export const mockTaskActivity = {
  task_id: 'task-ip1',
  entries: [
    {
      id: 'act-1',
      task_id: 'task-ip1',
      level: 'milestone',
      event_type: 'task_started',
      summary: 'Started by dispatcher',
      detail: { from_status: 'pending', to_status: 'running', actor: 'dispatcher' },
      created_at: '2026-03-28T10:00:00Z',
    },
  ],
}

export const mockAgents = [
  {
    id: 'agent-001',
    tenant_id: '00000000-0000-0000-0000-000000000000',
    name: 'Alex',
    role: 'cto',
    title: 'Chief Technology Officer',
    job_description: 'Leads technical architecture and engineering decisions',
    executor_type: 'bsgateway',
    executor_config: {},
    system_prompt: 'You are a senior technical leader.',
    skills: ['architecture-design', 'code-review'],
    capabilities: ['coding', 'analysis'],
    parent_agent_id: null,
    heartbeat_interval_seconds: 14400,
    heartbeat_enabled: true,
    last_heartbeat_at: '2026-04-05T10:00:00Z',
    monthly_budget_cents: 6000,
    current_month_spent_cents: 1200,
    status: 'online',
    dot: 'yellow',
    current_task: null,
    activity: '',
    is_active: true,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-05T10:00:00Z',
  },
  {
    id: 'agent-002',
    tenant_id: '00000000-0000-0000-0000-000000000000',
    name: 'Dev-1',
    role: 'engineer',
    title: 'Senior Engineer',
    job_description: null,
    executor_type: 'claude_code',
    executor_config: {},
    system_prompt: null,
    skills: ['git-ops'],
    capabilities: ['coding'],
    parent_agent_id: 'agent-001',
    heartbeat_interval_seconds: null,
    heartbeat_enabled: false,
    last_heartbeat_at: null,
    monthly_budget_cents: 30000,
    current_month_spent_cents: 4500,
    status: 'busy',
    dot: 'green',
    current_task: { id: 'task-ip1', title: 'Build agent status bar', status: 'running' },
    activity: '',
    is_active: true,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-05T12:00:00Z',
  },
  {
    id: 'agent-003',
    tenant_id: '00000000-0000-0000-0000-000000000000',
    name: 'Writer-Bot',
    role: 'content writer',
    title: null,
    job_description: 'Writes blog posts and documentation',
    executor_type: 'generic_llm',
    executor_config: {},
    system_prompt: null,
    skills: [],
    capabilities: ['writing'],
    parent_agent_id: null,
    heartbeat_interval_seconds: 28800,
    heartbeat_enabled: true,
    last_heartbeat_at: '2026-04-05T08:00:00Z',
    monthly_budget_cents: 10000,
    current_month_spent_cents: 200,
    status: 'online',
    dot: 'yellow',
    current_task: null,
    activity: '',
    is_active: true,
    created_at: '2026-04-02T00:00:00Z',
    updated_at: '2026-04-05T08:00:00Z',
  },
]

export const mockOrgChart = [
  {
    agent: mockAgents[0],
    children: [
      {
        agent: mockAgents[1],
        children: [],
      },
    ],
  },
  {
    agent: mockAgents[2],
    children: [],
  },
]

export const mockWorkers = [
  {
    id: 'worker-001',
    name: 'Mac Mini Runner',
    labels: ['macos', 'gpu'],
    status: 'online',
    last_heartbeat: '2026-04-05T12:00:00Z',
    capabilities: ['claude_code'],
    created_at: '2026-04-01T00:00:00Z',
  },
]

export const mockExecutorConfigs = [
  {
    id: 'exec-001',
    tenant_id: '00000000-0000-0000-0000-000000000000',
    name: 'Claude Sonnet 4',
    executor_type: 'claude_api',
    config: { api_key: 'sk-***', model: 'anthropic/claude-sonnet-4-20250514' },
    description: 'Default LLM API for coding tasks',
    is_default: true,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-01T00:00:00Z',
  },
  {
    id: 'exec-002',
    tenant_id: '00000000-0000-0000-0000-000000000000',
    name: 'Worker: Mac Mini Runner',
    executor_type: 'worker',
    config: { worker_id: 'worker-001' },
    description: 'Self-hosted worker (claude_code)',
    is_default: false,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-01T00:00:00Z',
  },
]

export const mockInstallToken = { has_token: true }

export const mockGoals = [
  {
    id: 'goal-001',
    tenant_id: '00000000-0000-0000-0000-000000000000',
    parent_goal_id: null,
    level: 'mission',
    title: 'Build the leading AI-native development ecosystem',
    description: null,
    project_id: null,
    agent_id: null,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-01T00:00:00Z',
  },
  {
    id: 'goal-002',
    tenant_id: '00000000-0000-0000-0000-000000000000',
    parent_goal_id: 'goal-001',
    level: 'department',
    title: 'Ship BSNexus v2.0',
    description: 'Complete Company OS evolution',
    project_id: null,
    agent_id: 'agent-001',
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-01T00:00:00Z',
  },
]

export const mockBudgetOverview = {
  total_budget_cents: 46000,
  total_spent_cents: 5900,
  total_remaining_cents: 40100,
  agent_summaries: [
    {
      agent_id: 'agent-001',
      agent_name: 'Alex',
      monthly_budget_cents: 6000,
      current_month_spent_cents: 1200,
      budget_remaining_cents: 4800,
      utilization_pct: 20.0,
    },
    {
      agent_id: 'agent-002',
      agent_name: 'Dev-1',
      monthly_budget_cents: 30000,
      current_month_spent_cents: 4500,
      budget_remaining_cents: 25500,
      utilization_pct: 15.0,
    },
    {
      agent_id: 'agent-003',
      agent_name: 'Writer-Bot',
      monthly_budget_cents: 10000,
      current_month_spent_cents: 200,
      budget_remaining_cents: 9800,
      utilization_pct: 2.0,
    },
  ],
}

export const mockCostRecords = [
  {
    id: 'cost-001',
    tenant_id: '00000000-0000-0000-0000-000000000000',
    agent_id: 'agent-001',
    task_id: null,
    amount_cents: 500,
    token_count: 12000,
    model_name: 'claude-3.5-sonnet',
    recorded_at: '2026-04-05T10:00:00Z',
  },
  {
    id: 'cost-002',
    tenant_id: '00000000-0000-0000-0000-000000000000',
    agent_id: 'agent-002',
    task_id: 'task-001',
    amount_cents: 200,
    token_count: 5000,
    model_name: 'gpt-4o',
    recorded_at: '2026-04-05T09:00:00Z',
  },
]

export const mockGlobalSettings = {
  llm_api_key: 'sk-***masked***',
  llm_model: 'anthropic/claude-sonnet-4-20250514',
  llm_base_url: null,
  default_executor_type: 'claude_api',
}

export const mockProjectChannels = [
  {
    id: 'chan-001',
    project_id: 'proj-001',
    kind: 'slack',
    external_channel_id: 'C0123456789',
    display_name: '#bsvibe-tax',
    is_active: true,
  },
]

export const mockDesignSystem = {
  project_id: 'proj-001',
  name: 'Default',
  tokens: { color: { primary: '#0ea5e9' } },
  components: {},
  patterns: {},
  brand_voice: null,
  path: 'design/system.bsd',
}

export const mockDesignScreens = [
  {
    project_id: 'proj-001',
    slug: 'login',
    path: 'design/screens/login.bsd',
    name: 'Login',
    route: '/login',
  },
]

export const mockMemories = [
  {
    id: 'mem-001',
    project_id: 'proj-001',
    agent_id: 'agent-001',
    category: 'decision',
    title: 'Use Tailwind for styling',
    content: 'The team standardized on Tailwind in week 1.',
    metadata: null,
  },
]
