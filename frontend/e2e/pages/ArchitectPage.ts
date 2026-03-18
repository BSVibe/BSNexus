import { Page } from '@playwright/test';
import { BasePage } from './BasePage';

export class ArchitectPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  async goto() {
    await this.page.goto('/architect');
  }

  async clickNewSession() {
    await this.click('button:has-text("New Session")');
    await this.page.waitForLoadState('networkidle');
  }

  async fillProjectName(name: string) {
    await this.fillInput('input[placeholder*="Project name"]', name);
  }

  async fillProjectDescription(description: string) {
    await this.fillInput('input[placeholder*="Description"]', description);
  }

  async fillRepoPath(repoPath: string) {
    await this.fillInput('input[placeholder*="Repository path"]', repoPath);
  }

  async clickCreateProject() {
    await this.click('button:has-text("Create")');
    await this.page.waitForLoadState('networkidle');
  }

  async sendMessage(message: string) {
    const input = this.page.locator('input[placeholder*="message"], textarea[placeholder*="message"]').first();
    await input.fill(message);
    await this.click('button[aria-label*="Send"], button:has-text("Send")');
    await this.page.waitForLoadState('networkidle');
  }

  async getSessionTitle(): Promise<string | null> {
    return this.getText('h1, h2');
  }

  async getLastMessage(): Promise<string | null> {
    const messages = this.page.locator('[data-testid="message"], div:has(> p)');
    const count = await messages.count();
    if (count === 0) return null;
    return messages.nth(count - 1).textContent();
  }

  async waitForSessionLoaded() {
    await this.page.waitForLoadState('networkidle');
    await this.page.waitForSelector('input[placeholder*="message"], textarea[placeholder*="message"]', { timeout: 5000 });
  }

  async isLoading(): Promise<boolean> {
    return this.isVisible('text=Loading, text=Thinking');
  }

  async getProjectCreatedMessage(): Promise<boolean> {
    return this.isVisible('text=Project created');
  }
}
