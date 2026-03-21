import { Page } from '@playwright/test';
import { BasePage } from './BasePage';

export class DashboardPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  async goto() {
    await this.page.goto('/');
  }

  async clickNewProjectButton() {
    await this.click('button:has-text("New Project")');
    await this.page.waitForNavigation();
  }

  async getProjectCount(): Promise<number> {
    const projects = await this.page.locator('[data-testid="project-card"]').count();
    return projects;
  }

  async getProjectByName(name: string) {
    return this.page.locator(`text=${name}`);
  }

  async clickProjectCard(projectName: string) {
    await this.click(`text=${projectName}`);
    await this.page.waitForNavigation();
  }

  async getTotalProjectsStat() {
    return this.getText('text=Total Projects');
  }

  async getTotalTasksStat() {
    return this.getText('text=Total Tasks');
  }

  async getCompletionRateStat() {
    return this.getText('text=Completion Rate');
  }

  async clickSelectMode() {
    await this.page.click('[title="Select mode"]');
  }

  async selectProject(projectName: string) {
    const projectCard = this.page.locator(`text=${projectName}`).first();
    const checkbox = projectCard.locator('xpath=./ancestor::div[contains(@class, "relative rounded-lg")]//div[@class and contains(@class, "w-5")]').first();
    await checkbox.click();
  }

  async clickSelectAll() {
    await this.click('button:has-text("All")');
  }

  async getSelectedCount(): Promise<string | null> {
    return this.getText('span:has-text("selected")');
  }

  async clickDeleteSelected() {
    await this.click('button:has-text("Delete")');
  }

  async confirmDelete() {
    await this.click('button:has-text("Delete"):nth-of-type(2)');
    await this.page.waitForLoadState('networkidle');
  }

  async cancelDelete() {
    await this.click('button:has-text("Cancel")');
  }

  async clickExitSelectMode() {
    await this.click('button:has-text("Cancel")');
  }

  async hoverProjectCard(projectName: string) {
    const card = this.page.locator(`text=${projectName}`).first();
    await card.hover();
  }

  async deleteProject(projectName: string) {
    await this.hoverProjectCard(projectName);
    await this.page.click('[title="Delete project"]');
    await this.page.waitForSelector('text=Delete Project');
  }

  async confirmProjectDelete() {
    const deleteButton = this.page.locator('button:has-text("Delete")').last();
    await deleteButton.click();
    await this.page.waitForLoadState('networkidle');
  }

  async isEmptyState() {
    return this.isVisible('text=No projects yet');
  }

  async waitForProjectsLoaded() {
    await this.page.waitForLoadState('networkidle');
  }
}
