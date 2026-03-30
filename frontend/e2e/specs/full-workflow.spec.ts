import { test, expect } from '@playwright/test'
import { setupMocks } from '../helpers/mock-api'

test.describe('Navigation & Workflow', () => {
  test.beforeEach(async ({ page }) => {
    await setupMocks(page)
  })

  test('sidebar shows navigation links', async ({ page }) => {
    await page.goto('/dashboard')

    await expect(page.getByRole('link', { name: /Dashboard/ })).toBeVisible()
    await expect(page.getByRole('link', { name: /New Project/ })).toBeVisible()
  })

  test('navigate from dashboard to architect via sidebar', async ({ page }) => {
    await page.goto('/dashboard')

    await page.getByRole('link', { name: /New Project/ }).click()
    await expect(page).toHaveURL(/\/architect/)
  })

  test('navigate from architect to dashboard via sidebar', async ({ page }) => {
    await page.goto('/architect')

    await page.getByRole('link', { name: /Dashboard/ }).click()
    await expect(page).toHaveURL(/\/dashboard/)
  })

  test('navigate from dashboard to project page', async ({ page }) => {
    await page.goto('/dashboard')

    await page.getByText('Test Project Alpha').click()
    await expect(page).toHaveURL(/\/projects\/proj-1/)

    // Project page should show project name
    await expect(page.getByText('Test Project Alpha')).toBeVisible()
  })

  test('full flow: dashboard -> project -> board with tasks', async ({ page }) => {
    await page.goto('/dashboard')

    // Click project card
    await page.getByText('Test Project Alpha').click()
    await expect(page).toHaveURL(/\/projects\/proj-1/)

    // Board should show tasks
    await expect(page.getByText('Implement authentication')).toBeVisible()
    await expect(page.getByText('Build API endpoints')).toBeVisible()
  })

  test('empty state CTA navigates to architect', async ({ page }) => {
    // Override with empty project list
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

    await page.getByRole('button', { name: 'Start with Architect' }).click()
    await expect(page).toHaveURL(/\/architect/)
  })
})
