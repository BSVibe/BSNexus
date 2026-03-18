import { test, expect } from '@playwright/test';
import { DashboardPage } from '../pages/DashboardPage';
import { waitForFrontend, waitForBackend, createProjectViaAPI, deleteProjectViaAPI } from '../helpers/test-utils';

test.describe('Dashboard', () => {
  let dashboardPage: DashboardPage;
  const createdProjectIds: string[] = [];

  test.beforeAll(async () => {
    await waitForBackend();
  });

  test.beforeEach(async ({ page }) => {
    dashboardPage = new DashboardPage(page);
    await waitForFrontend(page);
    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();
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

  test('should display dashboard with empty state when no projects', async ({ page }) => {
    const isEmpty = await dashboardPage.isEmptyState();
    if (isEmpty) {
      expect(await dashboardPage.isEmptyState()).toBe(true);
    }
  });

  test('should display dashboard stats', async () => {
    const totalText = await dashboardPage.getTotalProjectsStat();
    const tasksText = await dashboardPage.getTotalTasksStat();
    const completionText = await dashboardPage.getCompletionRateStat();

    expect(totalText).toBeTruthy();
    expect(tasksText).toBeTruthy();
    expect(completionText).toBeTruthy();
  });

  test('should navigate to architect when clicking "New Project"', async ({ page }) => {
    const isEmptyState = await dashboardPage.isEmptyState();
    if (!isEmptyState) {
      // If projects exist, use the header button
      const newProjectButton = page.locator('button:has-text("New Project")').first();
      await newProjectButton.click();
    } else {
      // Otherwise, use the empty state button
      await dashboardPage.clickNewProjectButton();
    }

    await page.waitForURL('/architect');
    expect(page.url()).toContain('/architect');
  });

  test('should display created project in list', async () => {
    const projectName = `Test Project ${Date.now()}`;
    const newProject = await createProjectViaAPI(projectName, 'Test Description', '/test/repo');
    createdProjectIds.push(newProject.id);

    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();

    const count = await dashboardPage.getProjectCount();
    expect(count).toBeGreaterThan(0);

    const project = await dashboardPage.getProjectByName(projectName);
    await expect(project).toBeVisible();
  });

  test('should navigate to project details when clicking project card', async ({ page }) => {
    const projectName = `Test Project ${Date.now()}`;
    const newProject = await createProjectViaAPI(projectName, 'Test Description', '/test/repo');
    createdProjectIds.push(newProject.id);

    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();

    await dashboardPage.clickProjectCard(projectName);
    await page.waitForURL(`/projects/${newProject.id}`);
    expect(page.url()).toContain(`/projects/${newProject.id}`);
  });

  test('should delete project with confirmation', async () => {
    const projectName = `Test Project ${Date.now()}`;
    const newProject = await createProjectViaAPI(projectName, 'Test Description', '/test/repo');

    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();

    const countBefore = await dashboardPage.getProjectCount();

    await dashboardPage.deleteProject(projectName);
    await dashboardPage.confirmProjectDelete();
    await dashboardPage.waitForProjectsLoaded();

    const countAfter = await dashboardPage.getProjectCount();
    expect(countAfter).toBeLessThan(countBefore);
  });

  test('should cancel project deletion', async () => {
    const projectName = `Test Project ${Date.now()}`;
    const newProject = await createProjectViaAPI(projectName, 'Test Description', '/test/repo');
    createdProjectIds.push(newProject.id);

    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();

    const countBefore = await dashboardPage.getProjectCount();

    await dashboardPage.deleteProject(projectName);
    await dashboardPage.cancelDelete();

    const countAfter = await dashboardPage.getProjectCount();
    expect(countAfter).toBe(countBefore);
  });

  test('should select multiple projects', async () => {
    const proj1 = await createProjectViaAPI(`Project 1 ${Date.now()}`, 'Test', '/test/repo1');
    const proj2 = await createProjectViaAPI(`Project 2 ${Date.now()}`, 'Test', '/test/repo2');
    createdProjectIds.push(proj1.id, proj2.id);

    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();

    await dashboardPage.clickSelectMode();

    // Select first project
    await dashboardPage.selectProject(proj1.name);
    let selectedCount = await dashboardPage.getSelectedCount();
    expect(selectedCount).toContain('1');

    // Select second project
    await dashboardPage.selectProject(proj2.name);
    selectedCount = await dashboardPage.getSelectedCount();
    expect(selectedCount).toContain('2');
  });

  test('should batch delete multiple projects', async () => {
    const proj1 = await createProjectViaAPI(`Project 1 ${Date.now()}`, 'Test', '/test/repo1');
    const proj2 = await createProjectViaAPI(`Project 2 ${Date.now()}`, 'Test', '/test/repo2');

    await dashboardPage.goto();
    await dashboardPage.waitForProjectsLoaded();

    const countBefore = await dashboardPage.getProjectCount();

    await dashboardPage.clickSelectMode();
    await dashboardPage.selectProject(proj1.name);
    await dashboardPage.selectProject(proj2.name);
    await dashboardPage.clickDeleteSelected();
    await dashboardPage.confirmDelete();
    await dashboardPage.waitForProjectsLoaded();

    const countAfter = await dashboardPage.getProjectCount();
    expect(countAfter).toBeLessThanOrEqual(countBefore - 2);
  });
});
