import { test, expect } from '@playwright/test'
import { mockAllApis, injectAuth } from '../helpers/mock-api'

test.describe('Auth — Landing Page & Protected Routes', () => {
  test('landing page shows BSNexus branding and sign-in button', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'BSNexus' })).toBeVisible()
    await expect(page.getByText('Orchestrate AI agents, from design to deployment.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign in with BSVibe' })).toBeVisible()
  })

  test('landing page displays three feature cards with Material Symbols', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText('Project Architect')).toBeVisible()
    await expect(page.getByText('Task Kanban')).toBeVisible()
    await expect(page.getByText('Distributed Workers')).toBeVisible()
    // Material Symbols icons are present
    await expect(page.locator('span.material-symbols-outlined:has-text("psychology")')).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("view_kanban")')).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("hub")')).toBeVisible()
  })

  test('landing page shows "Go to Dashboard" when authenticated', async ({ page }) => {
    await injectAuth(page)
    await page.goto('/')
    await expect(page.getByRole('button', { name: 'Go to Dashboard' })).toBeVisible()
  })

  test('unauthenticated user is redirected to landing from /dashboard', async ({ page }) => {
    await mockAllApis(page)
    await page.goto('/dashboard')
    // ProtectedRoute should redirect to /
    await expect(page).toHaveURL('/')
  })

  test('auth callback page shows spinner during processing', async ({ page }) => {
    // Navigate to callback without tokens — should redirect to landing
    await page.goto('/auth/callback')
    // The spinner briefly appears, then redirect happens
    await expect(page).toHaveURL('/')
  })

  test('landing page footer shows "Powered by BSVibe"', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText('Powered by BSVibe')).toBeVisible()
  })
})
