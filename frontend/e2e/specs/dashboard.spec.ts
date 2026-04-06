import { test, expect } from '@playwright/test'
import { setupPage } from '../helpers/mock-api'

test.describe('Dashboard — Stat Cards & Project Grid', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page, '/dashboard')
  })

  test('header displays "Dashboard" title', async ({ page }) => {
    await expect(page.locator('header').getByText('Dashboard')).toBeVisible()
  })

  test('renders four stat cards in bento grid', async ({ page }) => {
    await expect(page.getByText('Total Projects')).toBeVisible()
    await expect(page.getByText('Active Tasks')).toBeVisible()
    await expect(page.getByText('Completion Rate')).toBeVisible()
    await expect(page.getByText('Compute Cost')).toBeVisible()
  })

  test('stat card shows correct total projects count', async ({ page }) => {
    // 3 mock projects — locate the stat card containing "Total Projects" text
    const totalCard = page.locator('.bg-stitch-surface-low').filter({ hasText: 'Total Projects' }).first()
    await expect(totalCard.locator('.text-3xl')).toHaveText('3')
  })

  test('stat cards use Material Symbols icons', async ({ page }) => {
    await expect(page.locator('span.material-symbols-outlined:has-text("folder_open")').first()).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("bolt")').first()).toBeVisible()
    await expect(page.locator('span.material-symbols-outlined:has-text("payments")').first()).toBeVisible()
  })

  test('project cards are rendered in a grid', async ({ page }) => {
    // BSNexus appears in both sidebar and project card — scope to main content
    const main = page.locator('main')
    await expect(main.getByText('BSNexus').first()).toBeVisible()
    await expect(main.getByText('BSVibe Auth')).toBeVisible()
    await expect(main.getByText('Worker Agent')).toBeVisible()
  })

  test('project card shows status badge', async ({ page }) => {
    // Active project has status displayed
    const bsnexusCard = page.locator('a[href="/projects/proj-001"]')
    await expect(bsnexusCard.getByText('active')).toBeVisible()
  })

  test('project card shows phase count', async ({ page }) => {
    const bsnexusCard = page.locator('a[href="/projects/proj-001"]')
    await expect(bsnexusCard.getByText('2 phases')).toBeVisible()
  })

  test('project card shows task distribution bar with percentage', async ({ page }) => {
    const bsnexusCard = page.locator('a[href="/projects/proj-001"]')
    // 5 done out of 12 total = 42%
    await expect(bsnexusCard.getByText('42%')).toBeVisible()
    await expect(bsnexusCard.getByText('12 tasks')).toBeVisible()
  })

  test('project card shows bug count with bug_report icon', async ({ page }) => {
    const bsnexusCard = page.locator('a[href="/projects/proj-001"]')
    await expect(bsnexusCard.locator('span.material-symbols-outlined:has-text("bug_report")')).toBeVisible()
    await expect(bsnexusCard.getByText('2').first()).toBeVisible()
  })

  test('project card shows architect indicator when session exists', async ({ page }) => {
    const bsnexusCard = page.locator('a[href="/projects/proj-001"]')
    await expect(bsnexusCard.locator('span.material-symbols-outlined:has-text("architecture")')).toBeVisible()
    await expect(bsnexusCard.getByText('Architect')).toBeVisible()
  })

  test('project card shows event icon with date', async ({ page }) => {
    const bsnexusCard = page.locator('a[href="/projects/proj-001"]')
    await expect(bsnexusCard.locator('span.material-symbols-outlined:has-text("event")')).toBeVisible()
  })

  test('header has "New Project" button with gradient styling', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'New Project' })).toBeVisible()
  })

  test('header has "Import" button with folder_open icon', async ({ page }) => {
    const importBtn = page.getByRole('button', { name: 'Import' })
    await expect(importBtn).toBeVisible()
    await expect(importBtn.locator('span.material-symbols-outlined:has-text("folder_open")')).toBeVisible()
  })

  test('notifications bell icon is visible in header', async ({ page }) => {
    await expect(page.locator('header span.material-symbols-outlined:has-text("notifications")')).toBeVisible()
  })

  test('projects section header shows count', async ({ page }) => {
    // Use heading role to target the "Projects" section header specifically
    const main = page.locator('main')
    await expect(main.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await expect(main.getByText('3').first()).toBeVisible()
  })

  test('clicking project card navigates to project page', async ({ page }) => {
    await page.locator('a[href="/projects/proj-001"]').click()
    await expect(page).toHaveURL('/projects/proj-001')
  })
})
