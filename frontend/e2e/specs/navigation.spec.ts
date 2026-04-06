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

  test('sidebar has Dashboard nav item with dashboard icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const dashboardLink = sidebar.getByRole('link', { name: 'Dashboard' })
    await expect(dashboardLink).toBeVisible()
    await expect(dashboardLink.locator('span.material-symbols-outlined:has-text("dashboard")')).toBeVisible()
  })

  test('sidebar has Agents nav item with groups icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const agentsLink = sidebar.getByRole('link', { name: 'Agents' })
    await expect(agentsLink).toBeVisible()
    await expect(agentsLink.locator('span.material-symbols-outlined:has-text("groups")')).toBeVisible()
  })

  test('sidebar has Budget nav item with account_balance_wallet icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const budgetLink = sidebar.getByRole('link', { name: 'Budget' })
    await expect(budgetLink).toBeVisible()
    await expect(budgetLink.locator('span.material-symbols-outlined:has-text("account_balance_wallet")')).toBeVisible()
  })

  test('sidebar has Settings nav item with settings icon', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const settingsLink = sidebar.getByRole('link', { name: 'Settings' })
    await expect(settingsLink).toBeVisible()
    await expect(settingsLink.locator('span.material-symbols-outlined:has-text("settings")')).toBeVisible()
  })

  test('Dashboard link is active on /dashboard', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const dashboardLink = sidebar.getByRole('link', { name: 'Dashboard' })
    await expect(dashboardLink).toHaveClass(/font-semibold/)
  })

  test('Budget link is active on /budget', async ({ page }) => {
    await page.goto('/budget')
    const sidebar = page.locator('aside').first()
    const budgetLink = sidebar.getByRole('link', { name: 'Budget' })
    await expect(budgetLink).toHaveClass(/font-semibold/)
  })

  test('Settings link is active on /settings', async ({ page }) => {
    await page.goto('/settings')
    const sidebar = page.locator('aside').first()
    const settingsLink = sidebar.getByRole('link', { name: 'Settings' })
    await expect(settingsLink).toHaveClass(/font-semibold/)
  })

  test('Dashboard link remains active on /projects/:id', async ({ page }) => {
    await page.goto('/projects/proj-001')
    const sidebar = page.locator('aside').first()
    const dashboardLink = sidebar.getByRole('link', { name: 'Dashboard' })
    await expect(dashboardLink).toHaveClass(/font-semibold/)
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

  test('clicking Budget nav link navigates to /budget', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    await sidebar.getByRole('link', { name: 'Budget' }).click()
    await expect(page).toHaveURL('/budget')
  })

  test('clicking Settings nav link navigates to /settings', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    await sidebar.getByRole('link', { name: 'Settings' }).click()
    await expect(page).toHaveURL('/settings')
  })

  test('clicking Dashboard nav link navigates to /dashboard', async ({ page }) => {
    await page.goto('/agents')
    const sidebar = page.locator('aside').first()
    await sidebar.getByRole('link', { name: 'Dashboard' }).click()
    await expect(page).toHaveURL('/dashboard')
  })

  test('main content area has stitch-surface background', async ({ page }) => {
    const main = page.locator('main')
    await expect(main).toHaveClass(/bg-stitch-surface/)
  })

  test('layout is a flex container with sidebar and main content', async ({ page }) => {
    const layoutRoot = page.locator('div.flex.h-screen').first()
    await expect(layoutRoot).toBeVisible()
    await expect(layoutRoot.locator('aside')).toBeVisible()
    await expect(layoutRoot.locator('main')).toBeVisible()
  })
})
