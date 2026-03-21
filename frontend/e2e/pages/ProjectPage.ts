import { Page } from '@playwright/test';
import { BasePage } from './BasePage';

export class ProjectPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  async gotoProject(projectId: string) {
    await this.page.goto(`/projects/${projectId}`);
    await this.page.waitForLoadState('networkidle');
  }

  async getProjectName(): Promise<string | null> {
    return this.getText('h1, h2');
  }

  async getProjectStatus(): Promise<string | null> {
    return this.getText('span[class*="badge"]');
  }

  async getPhaseCount(): Promise<number> {
    const phases = this.page.locator('[data-testid="phase-item"], div:has(> span:has-text("Phase"))');
    return phases.count();
  }

  async clickCreatePhase() {
    await this.click('button:has-text("New Phase"), button:has-text("Add Phase")');
    await this.page.waitForSelector('input[placeholder*="Phase name"]', { timeout: 5000 });
  }

  async fillPhaseName(name: string) {
    await this.fillInput('input[placeholder*="Phase name"]', name);
  }

  async clickCreatePhaseButton() {
    await this.click('button:has-text("Create")');
    await this.page.waitForLoadState('networkidle');
  }

  async clickPhase(phaseName: string) {
    await this.click(`text=${phaseName}`);
    await this.page.waitForLoadState('networkidle');
  }

  async getTasksInPhase(phaseName: string): Promise<number> {
    const phase = this.page.locator(`text=${phaseName}`).first();
    const tasks = phase.locator('xpath=./ancestor::div[contains(@class, "bg-bg-card")]//div[contains(@class, "task")]');
    return tasks.count();
  }

  async clickEditProject() {
    await this.click('button[aria-label*="Edit"], button:has-text("Edit")');
    await this.page.waitForSelector('input[placeholder*="Project name"]', { timeout: 5000 });
  }

  async updateProjectName(name: string) {
    await this.page.fill('input[placeholder*="Project name"]', '');
    await this.fillInput('input[placeholder*="Project name"]', name);
  }

  async updateProjectDescription(description: string) {
    await this.page.fill('input[placeholder*="Description"]', '');
    await this.fillInput('input[placeholder*="Description"]', description);
  }

  async clickSaveProject() {
    await this.click('button:has-text("Save")');
    await this.page.waitForLoadState('networkidle');
  }

  async getBackButton() {
    return this.page.locator('button[aria-label*="Back"], button:has-text("Back")').first();
  }

  async goBack() {
    await this.getBackButton()?.click();
    await this.page.waitForNavigation();
  }
}
