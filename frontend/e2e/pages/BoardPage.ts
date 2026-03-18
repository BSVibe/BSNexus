import { Page } from '@playwright/test';
import { BasePage } from './BasePage';

export class BoardPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  async gotoProject(projectId: string) {
    await this.page.goto(`/projects/${projectId}`);
    await this.page.waitForLoadState('networkidle');
  }

  async getColumnTasks(columnName: string): Promise<number> {
    const column = this.page.locator(`text=${columnName}`).first();
    const cards = column.locator('xpath=./ancestor::div[contains(@class, "bg-bg-surface")]//div[contains(@class, "rounded-lg border")]');
    return cards.count();
  }

  async getTaskByTitle(taskTitle: string) {
    return this.page.locator(`text=${taskTitle}`).first();
  }

  async clickTask(taskTitle: string) {
    await this.click(`text=${taskTitle}`);
    await this.page.waitForLoadState('networkidle');
  }

  async dragTaskToColumn(taskTitle: string, columnName: string) {
    const task = this.page.locator(`text=${taskTitle}`).first();
    const column = this.page.locator(`text=${columnName}`).first().locator('xpath=./ancestor::div[contains(@class, "bg-bg-surface")]');

    await task.dragTo(column);
    await this.page.waitForLoadState('networkidle');
  }

  async getTaskStatus(taskTitle: string): Promise<string | null> {
    const task = this.page.locator(`text=${taskTitle}`).first();
    const badge = task.locator('xpath=./ancestor::div//span[contains(@class, "badge")]');
    return badge.textContent();
  }

  async getReadyTasksCount(): Promise<number> {
    return this.getColumnTasks('ready');
  }

  async getQueuedTasksCount(): Promise<number> {
    return this.getColumnTasks('queued');
  }

  async getInProgressTasksCount(): Promise<number> {
    return this.getColumnTasks('in_progress');
  }

  async getReviewTasksCount(): Promise<number> {
    return this.getColumnTasks('review');
  }

  async getDoneTasksCount(): Promise<number> {
    return this.getColumnTasks('done');
  }

  async getTotalTaskCount(): Promise<number> {
    const stats = this.page.locator('[data-testid="stat-card"]');
    const count = await stats.count();
    if (count === 0) return 0;
    return parseInt((await this.getText('[data-testid="stat-card"]:first-child')) || '0', 10);
  }

  async getCompletionPercentage(): Promise<number> {
    const text = await this.getText('text=Done tasks');
    const match = text?.match(/(\d+)%/);
    return match ? parseInt(match[1], 10) : 0;
  }

  async createTask(projectId: string, taskTitle: string) {
    await this.click('button:has-text("New Task"), button:has-text("Add Task")');
    await this.page.waitForSelector('input[placeholder*="Task title"]');
    await this.fillInput('input[placeholder*="Task title"]', taskTitle);
    await this.click('button:has-text("Create")');
    await this.page.waitForLoadState('networkidle');
  }

  async isColumnEmpty(columnName: string): Promise<boolean> {
    const column = this.page.locator(`text=${columnName}`).first();
    const cards = column.locator('xpath=./ancestor::div[contains(@class, "bg-bg-surface")]//div[contains(@class, "rounded-lg border")]');
    return (await cards.count()) === 0;
  }
}
