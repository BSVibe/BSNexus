let BASE_URL = 'http://localhost:8000/api/v1';

/**
 * Set the API base URL for all subsequent API calls.
 * This is typically called during test setup via playwright.config.ts or test fixtures.
 */
export function setApiBaseUrl(url: string) {
  BASE_URL = url;
}

/**
 * Get the current API base URL.
 */
export function getApiBaseUrl(): string {
  return BASE_URL;
}

export interface CreateProjectRequest {
  name: string;
  description: string;
  repo_path: string;
}

export interface CreatePhaseRequest {
  name: string;
  description?: string;
  order: number;
}

export interface CreateTaskRequest {
  project_id: string;
  phase_id: string;
  title: string;
  description?: string;
  priority?: string;
  depends_on?: string[];
  worker_prompt?: string;
  qa_prompt?: string;
}

export interface TransitionTaskRequest {
  new_status: string;
  actor: string;
  expected_version?: number;
  reason?: string;
}

export class APIClient {
  static async createProject(data: CreateProjectRequest) {
    const response = await fetch(`${BASE_URL}/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`Failed to create project: ${response.statusText}`);
    }
    return response.json();
  }

  static async listProjects() {
    const response = await fetch(`${BASE_URL}/projects`);
    if (!response.ok) {
      throw new Error(`Failed to list projects: ${response.statusText}`);
    }
    return response.json();
  }

  static async getProject(projectId: string) {
    const response = await fetch(`${BASE_URL}/projects/${projectId}`);
    if (!response.ok) {
      throw new Error(`Failed to get project: ${response.statusText}`);
    }
    return response.json();
  }

  static async deleteProject(projectId: string) {
    const response = await fetch(`${BASE_URL}/projects/${projectId}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      throw new Error(`Failed to delete project: ${response.statusText}`);
    }
  }

  static async batchDeleteProjects(projectIds: string[]) {
    const response = await fetch(`${BASE_URL}/projects/batch/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: projectIds }),
    });
    if (!response.ok) {
      throw new Error(`Failed to batch delete projects: ${response.statusText}`);
    }
  }

  static async createPhase(projectId: string, data: CreatePhaseRequest) {
    const response = await fetch(`${BASE_URL}/projects/${projectId}/phases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to create phase: ${response.statusText} - ${error}`);
    }
    return response.json();
  }

  static async activatePhase(_projectId: string, phaseId: string) {
    const response = await fetch(`http://localhost:8000/api/v1/projects/phases/${phaseId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'active' }),
    });
    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to activate phase: ${response.statusText} - ${error}`);
    }
    return response.json();
  }

  static async createTask(data: CreateTaskRequest) {
    const response = await fetch(`${BASE_URL}/tasks/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to create task: ${response.statusText} - ${error}`);
    }
    return response.json();
  }

  static async getTask(taskId: string) {
    const response = await fetch(`${BASE_URL}/tasks/${taskId}`);
    if (!response.ok) {
      throw new Error(`Failed to get task: ${response.statusText}`);
    }
    return response.json();
  }

  static async transitionTask(taskId: string, data: TransitionTaskRequest) {
    const response = await fetch(`${BASE_URL}/tasks/${taskId}/transition`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to transition task: ${response.statusText} - ${error}`);
    }
    return response.json();
  }

  static async getBoard(projectId: string) {
    const response = await fetch(`${BASE_URL}/board/${projectId}`);
    if (!response.ok) {
      throw new Error(`Failed to get board: ${response.statusText}`);
    }
    return response.json();
  }

  static async getProjectsSummary() {
    const response = await fetch(`${BASE_URL}/dashboard/projects-summary`);
    if (!response.ok) {
      throw new Error(`Failed to get projects summary: ${response.statusText}`);
    }
    return response.json();
  }

  static async waitForBackend(maxRetries = 30) {
    let retries = 0;
    while (retries < maxRetries) {
      try {
        const response = await fetch(`${BASE_URL}/projects`);
        if (response.ok || response.status === 401) {
          return true;
        }
      } catch (e) {
        // Continue retrying
      }
      await new Promise((r) => setTimeout(r, 1000));
      retries++;
    }
    throw new Error('Backend did not become available in time');
  }
}
