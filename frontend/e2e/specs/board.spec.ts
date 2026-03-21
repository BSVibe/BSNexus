import { test, expect } from '@playwright/test';
import { BoardPage } from '../pages/BoardPage';
import {
  waitForFrontend,
  waitForBackend,
  createProjectViaAPI,
  createPhaseViaAPI,
  createTaskViaAPI,
  transitionTaskViaAPI,
  deleteProjectViaAPI,
} from '../helpers/test-utils';

test.describe('Board', () => {
  let boardPage: BoardPage;
  const createdProjectIds: string[] = [];

  test.beforeAll(async () => {
    await waitForBackend();
  });

  test.beforeEach(async ({ page }) => {
    boardPage = new BoardPage(page);
    await waitForFrontend(page);
  });

  test.afterEach(async () => {
    for (const projectId of createdProjectIds) {
      try {
        await deleteProjectViaAPI(projectId);
      } catch {
        // Ignore cleanup errors
      }
    }
    createdProjectIds.length = 0;
  });

  test('should display empty board columns when project has no tasks', async () => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test', '/test/repo');
    createdProjectIds.push(project.id);

    await createPhaseViaAPI(project.id, 'Phase 1');

    await boardPage.gotoProject(project.id);

    const readyCount = await boardPage.getReadyTasksCount();
    const inProgressCount = await boardPage.getInProgressTasksCount();
    const reviewCount = await boardPage.getReviewTasksCount();
    const doneCount = await boardPage.getDoneTasksCount();

    expect(readyCount).toBe(0);
    expect(inProgressCount).toBe(0);
    expect(reviewCount).toBe(0);
    expect(doneCount).toBe(0);
  });

  test('should display created task in ready column', async () => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');

    // Create task - it should start in ready status
    const task = await createTaskViaAPI(project.id, phase.id, `Task ${Date.now()}`);

    await boardPage.gotoProject(project.id);

    const readyCount = await boardPage.getReadyTasksCount();
    expect(readyCount).toBeGreaterThan(0);

    const taskElement = await boardPage.getTaskByTitle(task.title);
    await expect(taskElement).toBeVisible();
  });

  test('should display multiple tasks across different columns', async () => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');

    // Create and transition tasks to different states
    const task1 = await createTaskViaAPI(project.id, phase.id, `Task 1 ${Date.now()}`);
    const task2 = await createTaskViaAPI(project.id, phase.id, `Task 2 ${Date.now()}`);
    const task3 = await createTaskViaAPI(project.id, phase.id, `Task 3 ${Date.now()}`);

    // Transition tasks to different states
    await transitionTaskViaAPI(task1.id, 'in_progress');
    await transitionTaskViaAPI(task2.id, 'in_progress');
    await transitionTaskViaAPI(task2.id, 'review');
    await transitionTaskViaAPI(task3.id, 'in_progress');
    await transitionTaskViaAPI(task3.id, 'review');
    await transitionTaskViaAPI(task3.id, 'done');

    await boardPage.gotoProject(project.id);

    const readyCount = await boardPage.getReadyTasksCount();
    const inProgressCount = await boardPage.getInProgressTasksCount();
    const reviewCount = await boardPage.getReviewTasksCount();
    const doneCount = await boardPage.getDoneTasksCount();

    expect(readyCount).toBe(0);
    expect(inProgressCount).toBe(1);
    expect(reviewCount).toBe(1);
    expect(doneCount).toBe(1);
  });

  test('should display task stats correctly', async () => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');

    // Create multiple tasks
    for (let i = 0; i < 3; i++) {
      await createTaskViaAPI(project.id, phase.id, `Task ${i} ${Date.now()}`);
    }

    await boardPage.gotoProject(project.id);

    const totalCount = await boardPage.getTotalTaskCount();
    expect(totalCount).toBe(3);
  });

  test('should update completion stats when task moves to done', async () => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');

    const task = await createTaskViaAPI(project.id, phase.id, `Task ${Date.now()}`);

    // Transition task to done
    await transitionTaskViaAPI(task.id, 'in_progress');
    await transitionTaskViaAPI(task.id, 'review');
    await transitionTaskViaAPI(task.id, 'done');

    await boardPage.gotoProject(project.id);

    const doneCount = await boardPage.getDoneTasksCount();
    expect(doneCount).toBe(1);
  });

  test('should handle task with dependencies (waiting status)', async () => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');

    // Create task 1 (no dependencies)
    const task1 = await createTaskViaAPI(project.id, phase.id, `Task 1 ${Date.now()}`);

    // Create task 2 (depends on task 1)
    const task2 = await createTaskViaAPI(project.id, phase.id, `Task 2 ${Date.now()}`, [task1.id]);

    await boardPage.gotoProject(project.id);

    // Task 2 should be in waiting status
    const status = await boardPage.getTaskStatus(task2.title);
    expect(status?.toLowerCase()).toContain('waiting');
  });

  test('should promote dependent task to ready when dependency is completed', async () => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');

    const task1 = await createTaskViaAPI(project.id, phase.id, `Task 1 ${Date.now()}`);
    const task2 = await createTaskViaAPI(project.id, phase.id, `Task 2 ${Date.now()}`, [task1.id]);

    // Complete task 1
    await transitionTaskViaAPI(task1.id, 'in_progress');
    await transitionTaskViaAPI(task1.id, 'review');
    await transitionTaskViaAPI(task1.id, 'done');

    await boardPage.gotoProject(project.id);

    // Task 2 should be promoted to ready
    const status = await boardPage.getTaskStatus(task2.title);
    expect(status?.toLowerCase()).toContain('ready');
  });
});
