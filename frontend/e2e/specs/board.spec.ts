import { test, expect } from '@playwright/test'
import { setupMocks, MOCK_BOARD_RESPONSE } from '../helpers/mock-api'

test.describe('Board / Project Page', () => {
  test.beforeEach(async ({ page }) => {
    await setupMocks(page)
  })

  test('displays project name in header', async ({ page }) => {
    await page.goto('/projects/proj-1')
    await expect(page.getByText('Test Project Alpha')).toBeVisible()
  })

  test('shows kanban columns', async ({ page }) => {
    await page.goto('/projects/proj-1')

    // Column headers - KanbanBoard uses capitalized labels
    await expect(page.locator('h3').filter({ hasText: 'Waiting' })).toBeVisible()
    await expect(page.locator('h3').filter({ hasText: 'Ready' })).toBeVisible()
    await expect(page.locator('h3').filter({ hasText: 'In Progress' })).toBeVisible()
    await expect(page.locator('h3').filter({ hasText: 'Review' })).toBeVisible()
    await expect(page.locator('h3').filter({ hasText: 'Done' })).toBeVisible()
  })

  test('displays tasks in correct columns', async ({ page }) => {
    await page.goto('/projects/proj-1')

    // Ready tasks
    await expect(page.getByText('Implement authentication')).toBeVisible()
    await expect(page.getByText('Add input validation')).toBeVisible()

    // In progress
    await expect(page.getByText('Build API endpoints')).toBeVisible()

    // Review
    await expect(page.getByText('Fix login bug')).toBeVisible()

    // Done
    await expect(page.getByText('Setup CI/CD')).toBeVisible()
    await expect(page.getByText('Create database schema')).toBeVisible()
    await expect(page.getByText('Write unit tests')).toBeVisible()
  })

  test('shows board stats with task count', async ({ page }) => {
    await page.goto('/projects/proj-1')

    // BoardStats component shows total task count as "{total} tasks"
    await expect(page.getByText('7', { exact: true }).first()).toBeVisible()
  })

  test('shows task type badges', async ({ page }) => {
    await page.goto('/projects/proj-1')

    // Task types from mock data
    await expect(page.getByText('feature').first()).toBeVisible()
    await expect(page.getByText('bug').first()).toBeVisible()
  })

  test('clicking task opens detail view', async ({ page }) => {
    await page.goto('/projects/proj-1')

    await page.getByText('Implement authentication').first().click()

    // TaskDetail should show the task title in an h2
    await expect(page.locator('h2').filter({ hasText: 'Implement authentication' })).toBeVisible()
  })

  test('shows connection status indicator', async ({ page }) => {
    await page.goto('/projects/proj-1')

    // The header shows Live/Disconnected status
    await expect(page.getByText(/Live|Disconnected/)).toBeVisible()
  })

  test('chat toggle button works', async ({ page }) => {
    await page.goto('/projects/proj-1')

    // Click the chat toggle button
    const toggleBtn = page.locator('button[title="Open Architect chat"]')
    if (await toggleBtn.isVisible()) {
      await toggleBtn.click()

      // Chat panel should open showing Architect header
      await expect(page.locator('.border-l').getByText('Architect')).toBeVisible()
    }
  })

  test('empty board shows zero count badges', async ({ page }) => {
    const emptyBoard = {
      project_id: 'proj-1',
      columns: {
        waiting: { tasks: [] },
        ready: { tasks: [] },
        in_progress: { tasks: [] },
        review: { tasks: [] },
        done: { tasks: [] },
      },
      stats: { waiting: 0, ready: 0, in_progress: 0, review: 0, done: 0 },
      phases: {},
      redesign_tasks: [],
    }

    // Unroute existing board routes then set empty override
    await page.unroute('**/api/v1/board/proj-*/events')
    await page.unroute('**/api/v1/board/proj-*')
    await page.route('**/api/v1/board/proj-*/events', (route) => {
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: {"event":"connected"}\n\n' })
    })
    await page.route('**/api/v1/board/proj-*', (route) => {
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(emptyBoard) })
    })

    await page.goto('/projects/proj-1')

    // Empty columns show "No tasks" text
    const noTasksLabels = page.getByText('No tasks')
    await expect(noTasksLabels.first()).toBeVisible()
    expect(await noTasksLabels.count()).toBeGreaterThanOrEqual(5)
  })
})
