import { test, expect } from '@playwright/test';

// Parameterized: every page must load (HTTP 200) without JavaScript errors.
const pages = ['portal', 'builder', 'browse', 'invites', 'stats'];

for (const name of pages) {
  test(`${name}.html loads without JS errors`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const response = await page.goto(`/static/${name}.html`);
    expect(response?.status()).toBe(200);
    await expect(page.locator('body')).toBeVisible();
    expect(errors).toEqual([]);
  });
}
