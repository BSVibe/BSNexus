import { test, expect } from '@playwright/test'
import { setupPage, injectAuth, mockAllApis } from '../helpers/mock-api'

test.describe('Project — Detail Page & Board Integration', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page, '/projects/proj-001')
  })

  test('shows fallback when no project selected', async ({ page }) => {
    await injectAuth(page)
    await mockAllApis(page)
    await page.goto('/projects/', { waitUntil: 'networkidle' })
    await expect(page.getByText('Select a project from the Dashboard')).toBeVisible()
  })

  test('displays project name as large heading', async ({ page }) => {
    await expect(page.getByText('BSNexus').first()).toBeVisible()
  })

  test('has chat sidebar toggle button', async ({ page }) => {
    const toggleBtn = page.locator('button[title="Hide chat"]').or(
      page.locator('button[title="Show chat"]'),
    )
    await expect(toggleBtn).toBeVisible()
  })

  test('board stats section shows Completion card', async ({ page }) => {
    await expect(page.getByText('Completion')).toBeVisible()
  })

  test('board stats section shows Total Tasks card', async ({ page }) => {
    await expect(page.getByText('Total Tasks')).toBeVisible()
  })

  test('board stats section shows Status Breakdown card', async ({ page }) => {
    await expect(page.getByText('Status Breakdown')).toBeVisible()
  })

  test('board stats section shows Project Status card', async ({ page }) => {
    await expect(page.getByText('Project Status')).toBeVisible()
  })

  test('kanban board columns are rendered within project page', async ({ page }) => {
    await expect(page.getByText('Waiting', { exact: false }).first()).toBeVisible()
    await expect(page.getByText('Ready', { exact: false }).first()).toBeVisible()
    await expect(page.getByText('In Progress', { exact: false }).first()).toBeVisible()
    await expect(page.getByText('Review', { exact: false }).first()).toBeVisible()
    await expect(page.getByText('Done', { exact: false }).first()).toBeVisible()
  })

  test('board stats uses Material Symbols bolt icon', async ({ page }) => {
    await expect(page.locator('.material-symbols-outlined:has-text("bolt")').first()).toBeVisible()
  })

  test('board stats uses cloud_done icon', async ({ page }) => {
    await expect(page.locator('.material-symbols-outlined:has-text("cloud_done")')).toBeVisible()
  })
})
