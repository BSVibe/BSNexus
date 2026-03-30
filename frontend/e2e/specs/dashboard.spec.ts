import { test, expect } from '@playwright/test'
import {
  setupMocks,
  MOCK_PROJECTS,
  MOCK_PROJECTS_SUMMARY,
} from '../helpers/mock-api'

test.describe('Dashboard Page', () => {
  test.beforeEach(async ({ page }) => {
    await setupMocks(page)
  })

  test('displays stat cards with correct values', async ({ page }) => {
    await page.goto('/dashboard')

    // StatCard labels
    await expect(page.getByText('Projects', { exact: true })).toBeVisible()
    await expect(page.getByText('Tasks', { exact: true })).toBeVisible()
    await expect(page.getByText('Bugs', { exact: true })).toBeVisible()
    await expect(page.getByText('Completion', { exact: true })).toBeVisible()

    // Values derived from mock data
    await expect(page.getByText('2', { exact: true }).first()).toBeVisible() // total projects
    await expect(page.getByText('7', { exact: true }).first()).toBeVisible() // total tasks
    await expect(page.getByText('43%').first()).toBeVisible() // completion rate
  })

  test('displays project cards', async ({ page }) => {
    await page.goto('/dashboard')

    await expect(page.getByText('Test Project Alpha')).toBeVisible()
    await expect(page.getByText('Test Project Beta')).toBeVisible()
    await expect(page.getByText('A test project for E2E testing')).toBeVisible()
  })

  test('shows project status badges', async ({ page }) => {
    await page.goto('/dashboard')

    await expect(page.getByText('active', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('design', { exact: true }).first()).toBeVisible()
  })

  test('navigates to project on card click', async ({ page }) => {
    await page.goto('/dashboard')

    await page.getByText('Test Project Alpha').click()
    await expect(page).toHaveURL(/\/projects\/proj-1/)
  })

  test('navigates to architect on New Project button', async ({ page }) => {
    await page.goto('/dashboard')

    await page.getByRole('button', { name: /New Project/ }).click()
    await expect(page).toHaveURL(/\/architect/)
  })

  test('shows empty state when no projects', async ({ page }) => {
    // Override with empty list
    await page.route('**/api/v1/projects', (route, request) => {
      if (request.method() === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      }
      return route.continue()
    })
    await page.route('**/api/v1/dashboard/projects-summary', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    )

    await page.goto('/dashboard')

    await expect(page.getByText('No projects yet')).toBeVisible()
    await expect(page.getByText('Start with Architect')).toBeVisible()
  })

  test('shows task distribution on project card', async ({ page }) => {
    await page.goto('/dashboard')

    // Mock summary has 7 tasks for proj-1
    await expect(page.getByText('7 tasks', { exact: true })).toBeVisible()
  })

  test('delete project shows confirmation modal', async ({ page }) => {
    await page.goto('/dashboard')

    // Hover over a project card to reveal delete button
    const card = page.locator('.bg-bg-card').filter({ hasText: 'Test Project Alpha' })
    await card.hover()

    // Click the delete button (the X icon)
    const deleteBtn = card.locator('button[title="Delete project"]')
    await deleteBtn.click()

    // Modal appears
    await expect(page.getByText('Delete Project')).toBeVisible()
    await expect(page.getByText(/Are you sure you want to delete/)).toBeVisible()
  })

  test('cancel delete closes modal', async ({ page }) => {
    await page.goto('/dashboard')

    const card = page.locator('.bg-bg-card').filter({ hasText: 'Test Project Alpha' })
    await card.hover()
    await card.locator('button[title="Delete project"]').click()

    await expect(page.getByText('Delete Project')).toBeVisible()

    // Click cancel
    await page.getByRole('button', { name: 'Cancel' }).click()

    // Modal should close
    await expect(page.getByText(/Are you sure you want to delete/)).not.toBeVisible()
  })

  test('select mode enables multi-select', async ({ page }) => {
    await page.goto('/dashboard')

    // Enter select mode via the ListChecks icon button
    await page.locator('button[title="Select mode"]').click()

    // Batch action buttons should appear
    await expect(page.getByRole('button', { name: 'All' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible()
  })

  test('select all and batch delete', async ({ page }) => {
    await page.goto('/dashboard')

    // Enter select mode
    await page.locator('button[title="Select mode"]').click()

    // Select all
    await page.getByRole('button', { name: 'All' }).click()

    // Should show selected count
    await expect(page.getByText('2 selected')).toBeVisible()

    // Delete button appears
    await page.getByRole('button', { name: 'Delete' }).click()

    // Batch delete modal
    await expect(page.getByText('Delete Projects')).toBeVisible()
    await expect(page.getByText('2 projects', { exact: true })).toBeVisible()
  })
})
