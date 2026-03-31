import { test, expect } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

test.describe('Navigation — Sidebar & Active States', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page, '/dashboard')
  })

  test('sidebar displays BSNexus logo with architecture icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    await expect(sidebar.getByText('BSNexus')).toBeVisible()
    await expect(sidebar.locator('span.material-symbols-outlined:has-text("architecture")').first()).toBeVisible()
  })

  test('sidebar shows "Agent Orchestrator" subtitle', async ({ page }) => {
    await expect(page.locator('aside').first().getByText('Agent Orchestrator')).toBeVisible()
  })

  test('sidebar has Projects nav item with folder_open icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const projectsLink = sidebar.getByRole('link', { name: 'Projects' })
    await expect(projectsLink).toBeVisible()
    await expect(projectsLink.locator('span.material-symbols-outlined:has-text("folder_open")')).toBeVisible()
  })

  test('sidebar has Architect nav item with architecture icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const architectLink = sidebar.getByRole('link', { name: 'Architect' })
    await expect(architectLink).toBeVisible()
    await expect(architectLink.locator('span.material-symbols-outlined:has-text("architecture")')).toBeVisible()
  })

  test('sidebar has Settings button with settings icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const settingsBtn = sidebar.getByRole('button', { name: 'Settings' })
    await expect(settingsBtn).toBeVisible()
    await expect(settingsBtn.locator('span.material-symbols-outlined:has-text("settings")')).toBeVisible()
  })

  test('Projects link is active on /dashboard', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const projectsLink = sidebar.getByRole('link', { name: 'Projects' })
    // Active state has font-semibold and filled icon
    await expect(projectsLink).toHaveClass(/font-semibold/)
  })

  test('Architect link is active on /architect', async ({ page }) => {
    await page.goto('/architect')
    const sidebar = page.locator('aside').first()
    const architectLink = sidebar.getByRole('link', { name: 'Architect' })
    await expect(architectLink).toHaveClass(/font-semibold/)
  })

  test('Projects link remains active on /projects/:id', async ({ page }) => {
    await page.goto('/projects/proj-001')
    const sidebar = page.locator('aside').first()
    const projectsLink = sidebar.getByRole('link', { name: 'Projects' })
    await expect(projectsLink).toHaveClass(/font-semibold/)
  })

  test('sidebar shows user profile section with person icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    await expect(sidebar.locator('span.material-symbols-outlined:has-text("person")')).toBeVisible()
  })

  test('sidebar shows user email username', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    await expect(sidebar.getByText('dev', { exact: true })).toBeVisible()
  })

  test('sidebar shows logout button with logout icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const logoutBtn = sidebar.locator('button[title="Logout"]')
    await expect(logoutBtn).toBeVisible()
    await expect(logoutBtn.locator('span.material-symbols-outlined:has-text("logout")')).toBeVisible()
  })

  test('clicking Architect nav link navigates to /architect', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    await sidebar.getByRole('link', { name: 'Architect' }).click()
    await expect(page).toHaveURL('/architect')
  })

  test('clicking Projects nav link navigates to /dashboard', async ({ page }) => {
    // First go to architect, then click Projects
    await page.goto('/architect')
    const sidebar = page.locator('aside').first()
    await sidebar.getByRole('link', { name: 'Projects' }).click()
    await expect(page).toHaveURL('/dashboard')
  })

  test('main content area has stitch-surface background', async ({ page }) => {
    const main = page.locator('main')
    await expect(main).toHaveClass(/bg-stitch-surface/)
  })

  test('layout is a flex container with sidebar and main content', async ({ page }) => {
    const layoutRoot = page.locator('div.flex.h-screen').first()
    await expect(layoutRoot).toBeVisible()
    // Sidebar (aside) + main are siblings
    await expect(layoutRoot.locator('aside')).toBeVisible()
    await expect(layoutRoot.locator('main')).toBeVisible()
  })
})
