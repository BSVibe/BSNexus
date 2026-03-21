import { Page } from '@playwright/test';

export class BasePage {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  async goto(url: string) {
    await this.page.goto(url);
  }

  async fillInput(selector: string, text: string) {
    await this.page.fill(selector, text);
  }

  async click(selector: string | { selector?: string; text?: string }) {
    if (typeof selector === 'string') {
      await this.page.click(selector);
    } else if (selector.text) {
      await this.page.click(`text=${selector.text}`);
    }
  }

  async getText(selector: string) {
    return await this.page.textContent(selector);
  }

  async isVisible(selector: string) {
    return await this.page.isVisible(selector);
  }

  async waitForNavigation(fn: () => Promise<void>) {
    await Promise.all([this.page.waitForNavigation(), fn()]);
  }

  async waitForSelector(selector: string) {
    await this.page.waitForSelector(selector);
  }
}
