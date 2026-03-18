import { test, expect } from '@playwright/test';
import { DashboardPage } from '../pages/DashboardPage';
import { BoardPage } from '../pages/BoardPage';
import { ProjectPage } from '../pages/ProjectPage';
import {
  waitForFrontend,
  waitForBackend,
  createProjectViaAPI,
  createPhaseViaAPI,
  createTaskViaAPI,
  transitionTaskViaAPI,
  deleteProjectViaAPI,
} from '../helpers/test-utils';

test.describe('Full Workflow', () => {
  let dashboardPage: DashboardPage;
  let boardPage: BoardPage;
  let projectPage: ProjectPage;
  const createdProjectIds: string[] = [];

  test.beforeAll(async () => {
    await waitForBackend();
  });

  test.beforeEach(async ({ page }) => {
    dashboardPage = new DashboardPage(page);
    boardPage = new BoardPage(page);
    projectPage = new ProjectPage(page);
    await waitForFrontend(page);
  });

  test.afterEach(async () => {
    for (const projectId of createdProjectIds) {
      try {
        await deleteProjectViaAPI(projectId);
      } catch (e) {
        // Ignore cleanup errors
      }
    }
    createdProjectIds.length = 0;
  });

  test('should navigate from dashboard to project to board', async ({ page }) => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test Project', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');

    // Start at dashboard
    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();

    // Navigate to project
    await dashboardPage.clickProjectCard(project.name);
    await page.waitForURL(`/projects/${project.id}`);

    expect(page.url()).toContain(`/projects/${project.id}`);

    // Verify we're on the project page
    const projectName = await projectPage.getProjectName();
    expect(projectName?.toLowerCase()).toContain(project.name.toLowerCase());
  });

  test('should create task and transition through states', async ({ page }) => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test Project', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');
    const task = await createTaskViaAPI(project.id, phase.id, `Task ${Date.now()}`);

    // Navigate to board
    await boardPage.gotoProject(project.id);

    // Verify task is in ready state
    let readyCount = await boardPage.getReadyTasksCount();
    expect(readyCount).toBe(1);

    // Transition task to queued
    await transitionTaskViaAPI(task.id, 'queued');
    await page.reload();

    let queuedCount = await boardPage.getQueuedTasksCount();
    expect(queuedCount).toBe(1);

    // Transition task to in_progress
    await transitionTaskViaAPI(task.id, 'in_progress');
    await page.reload();

    let inProgressCount = await boardPage.getInProgressTasksCount();
    expect(inProgressCount).toBe(1);

    // Transition task to review
    await transitionTaskViaAPI(task.id, 'review');
    await page.reload();

    let reviewCount = await boardPage.getReviewTasksCount();
    expect(reviewCount).toBe(1);

    // Transition task to done
    await transitionTaskViaAPI(task.id, 'done');
    await page.reload();

    let doneCount = await boardPage.getDoneTasksCount();
    expect(doneCount).toBe(1);
  });

  test('should handle dependent tasks workflow', async ({ page }) => {
    const project = await createProjectViaAPI(`Project ${Date.now()}`, 'Test Project', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');

    // Create task A (no dependencies)
    const taskA = await createTaskViaAPI(project.id, phase.id, `Task A ${Date.now()}`);

    // Create task B (depends on A)
    const taskB = await createTaskViaAPI(project.id, phase.id, `Task B ${Date.now()}`, [taskA.id]);

    // Create task C (depends on B)
    const taskC = await createTaskViaAPI(project.id, phase.id, `Task C ${Date.now()}`, [taskB.id]);

    // Navigate to board
    await boardPage.gotoProject(project.id);

    // Verify initial state: A=ready, B=waiting, C=waiting
    let readyCount = await boardPage.getReadyTasksCount();
    expect(readyCount).toBe(1);

    // Complete task A
    await transitionTaskViaAPI(taskA.id, 'queued');
    await transitionTaskViaAPI(taskA.id, 'in_progress');
    await transitionTaskViaAPI(taskA.id, 'review');
    await transitionTaskViaAPI(taskA.id, 'done');

    await page.reload();

    // Now B should be ready
    readyCount = await boardPage.getReadyTasksCount();
    expect(readyCount).toBe(1);

    const statusB = await boardPage.getTaskStatus(taskB.title);
    expect(statusB?.toLowerCase()).toContain('ready');

    // Complete task B
    await transitionTaskViaAPI(taskB.id, 'queued');
    await transitionTaskViaAPI(taskB.id, 'in_progress');
    await transitionTaskViaAPI(taskB.id, 'review');
    await transitionTaskViaAPI(taskB.id, 'done');

    await page.reload();

    // Now C should be ready
    const statusC = await boardPage.getTaskStatus(taskC.title);
    expect(statusC?.toLowerCase()).toContain('ready');
  });

  test('should complete full project lifecycle', async ({ page }) => {
    const projectName = `Full Lifecycle ${Date.now()}`;
    const project = await createProjectViaAPI(projectName, 'Complete Project', '/test/repo');
    createdProjectIds.push(project.id);

    // Create phases
    const phase1 = await createPhaseViaAPI(project.id, 'Phase 1', 1);
    const phase2 = await createPhaseViaAPI(project.id, 'Phase 2', 2);

    // Create tasks in phase 1
    const task1a = await createTaskViaAPI(project.id, phase1.id, 'Task 1-A');
    const task1b = await createTaskViaAPI(project.id, phase1.id, 'Task 1-B');

    // Create tasks in phase 2
    const task2a = await createTaskViaAPI(project.id, phase2.id, 'Task 2-A', [task1a.id, task1b.id]);

    // Verify on dashboard
    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();

    const projectCard = await dashboardPage.getProjectByName(projectName);
    await expect(projectCard).toBeVisible();

    // Complete phase 1 tasks
    await transitionTaskViaAPI(task1a.id, 'queued');
    await transitionTaskViaAPI(task1a.id, 'in_progress');
    await transitionTaskViaAPI(task1a.id, 'review');
    await transitionTaskViaAPI(task1a.id, 'done');

    await transitionTaskViaAPI(task1b.id, 'queued');
    await transitionTaskViaAPI(task1b.id, 'in_progress');
    await transitionTaskViaAPI(task1b.id, 'review');
    await transitionTaskViaAPI(task1b.id, 'done');

    // Complete phase 2 task
    await transitionTaskViaAPI(task2a.id, 'queued');
    await transitionTaskViaAPI(task2a.id, 'in_progress');
    await transitionTaskViaAPI(task2a.id, 'review');
    await transitionTaskViaAPI(task2a.id, 'done');

    // Verify on board
    await boardPage.gotoProject(project.id);

    const doneCount = await boardPage.getDoneTasksCount();
    expect(doneCount).toBe(3);

    const readyCount = await boardPage.getReadyTasksCount();
    expect(readyCount).toBe(0);
  });

  test('should handle project creation from empty state', async ({ page }) => {
    // Create a project through API
    const project = await createProjectViaAPI(`New Project ${Date.now()}`, 'Description', '/test/repo');
    createdProjectIds.push(project.id);

    // Navigate to dashboard
    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();

    // Verify project appears
    const projects = await page.locator('[data-testid="project-card"], div:has(> h3)');
    const count = await projects.count();
    expect(count).toBeGreaterThan(0);

    // Navigate to project
    await dashboardPage.clickProjectCard(project.name);
    await page.waitForURL(`/projects/${project.id}`);

    // Verify project details
    const name = await projectPage.getProjectName();
    expect(name?.toLowerCase()).toContain(project.name.toLowerCase());
  });

  test('should display stats correctly after task completion', async ({ page }) => {
    const project = await createProjectViaAPI(`Stats Test ${Date.now()}`, 'Test', '/test/repo');
    createdProjectIds.push(project.id);

    const phase = await createPhaseViaAPI(project.id, 'Phase 1');

    // Create 3 tasks
    const task1 = await createTaskViaAPI(project.id, phase.id, `Task 1 ${Date.now()}`);
    const task2 = await createTaskViaAPI(project.id, phase.id, `Task 2 ${Date.now()}`);
    const task3 = await createTaskViaAPI(project.id, phase.id, `Task 3 ${Date.now()}`);

    // Navigate to board
    await boardPage.gotoProject(project.id);

    // Verify initial state: 3 ready tasks
    let readyCount = await boardPage.getReadyTasksCount();
    expect(readyCount).toBe(3);

    // Complete 2 tasks
    await transitionTaskViaAPI(task1.id, 'queued');
    await transitionTaskViaAPI(task1.id, 'in_progress');
    await transitionTaskViaAPI(task1.id, 'review');
    await transitionTaskViaAPI(task1.id, 'done');

    await transitionTaskViaAPI(task2.id, 'queued');
    await transitionTaskViaAPI(task2.id, 'in_progress');
    await transitionTaskViaAPI(task2.id, 'review');
    await transitionTaskViaAPI(task2.id, 'done');

    await page.reload();

    // Verify stats
    const totalCount = await boardPage.getTotalTaskCount();
    expect(totalCount).toBe(3);

    const doneCount = await boardPage.getDoneTasksCount();
    expect(doneCount).toBe(2);

    const readyCountAfter = await boardPage.getReadyTasksCount();
    expect(readyCountAfter).toBe(1);
  });
});
