import { test, expect } from '@playwright/test'
import { setupAuth, mockAuthRoutes } from '../helpers/mock-api'

test.describe('Authentication Flow', () => {
  test('landing page shown when not authenticated', async ({ page }) => {
    await page.goto('/')

    // Landing page elements
    await expect(page.getByText('BSNexus')).toBeVisible()
  })

  test('unauthenticated user redirected from protected routes', async ({ page }) => {
    await page.goto('/dashboard')

    // Should redirect to landing page
    await expect(page).toHaveURL('/')
  })

  test('unauthenticated user redirected from project page', async ({ page }) => {
    await page.goto('/projects/proj-1')

    await expect(page).toHaveURL('/')
  })

  test('authenticated user can access dashboard', async ({ page }) => {
    await setupAuth(page)
    await mockAuthRoutes(page)

    // Mock the API calls that dashboard makes
    await page.route('**/api/v1/projects', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    )
    await page.route('**/api/v1/dashboard/projects-summary', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    )

    await page.goto('/dashboard')

    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  })

  test('authenticated user can access architect page', async ({ page }) => {
    await setupAuth(page)
    await mockAuthRoutes(page)

    await page.route('**/api/v1/architect/sessions', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    )

    await page.goto('/architect')

    await expect(page.getByRole('heading', { name: 'Architect', exact: true })).toBeVisible()
  })
})
