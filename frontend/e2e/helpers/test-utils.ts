import { Page, expect } from '@playwright/test';
import { getApiBaseUrl } from './api-client';

export async function waitForBackend(maxRetries = 30) {
  const BASE_URL = getApiBaseUrl();
  let retries = 0;
  while (retries < maxRetries) {
    try {
      const response = await fetch(`${BASE_URL}/projects`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
      });
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

export async function waitForFrontend(page: Page, maxRetries = 30) {
  let retries = 0;
  while (retries < maxRetries) {
    try {
      await page.goto(process.env.FRONTEND_BASE_URL || 'http://localhost:3000', { waitUntil: 'domcontentloaded', timeout: 5000 });
      return true;
    } catch (e) {
      // Continue retrying
    }
    await new Promise((r) => setTimeout(r, 1000));
    retries++;
  }
  throw new Error('Frontend did not become available in time');
}

export async function createProjectViaAPI(
  projectName: string,
  description: string = 'Test project',
  repoPath: string = '/test/repo'
) {
  const response = await fetch(`${getApiBaseUrl()}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: projectName,
      description,
      repo_path: repoPath,
    }),
  });

  if (!response.ok) {
    throw new Error(`Failed to create project: ${response.statusText}`);
  }

  return response.json();
}

export async function createPhaseViaAPI(projectId: string, phaseName: string, order: number = 1) {
  const response = await fetch(`${getApiBaseUrl()}/projects/${projectId}/phases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: phaseName,
      description: `Phase ${order}`,
      order,
    }),
  });

  if (!response.ok) {
    throw new Error(`Failed to create phase: ${response.statusText}`);
  }

  return response.json();
}

export async function createTaskViaAPI(
  projectId: string,
  phaseId: string,
  taskTitle: string,
  dependsOn: string[] = []
) {
  const response = await fetch(`${getApiBaseUrl()}/tasks/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: projectId,
      phase_id: phaseId,
      title: taskTitle,
      description: `Task: ${taskTitle}`,
      priority: 'medium',
      depends_on: dependsOn,
      worker_prompt: 'Work on this task',
      qa_prompt: 'Review this task',
    }),
  });

  if (!response.ok) {
    throw new Error(`Failed to create task: ${response.statusText}`);
  }

  return response.json();
}

export async function transitionTaskViaAPI(taskId: string, newStatus: string, actor: string = 'test') {
  const response = await fetch(`${getApiBaseUrl()}/tasks/${taskId}/transition`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      new_status: newStatus,
      actor,
    }),
  });

  if (!response.ok) {
    throw new Error(`Failed to transition task: ${response.statusText}`);
  }

  return response.json();
}

export async function deleteProjectViaAPI(projectId: string) {
  const response = await fetch(`${getApiBaseUrl()}/projects/${projectId}`, {
    method: 'DELETE',
  });

  if (!response.ok) {
    throw new Error(`Failed to delete project: ${response.statusText}`);
  }
}

export async function expectToSeeText(page: Page, text: string) {
  await expect(page.locator(`text=${text}`)).toBeVisible({ timeout: 5000 });
}

export async function expectNotToSeeText(page: Page, text: string) {
  await expect(page.locator(`text=${text}`)).not.toBeVisible({ timeout: 5000 });
}
