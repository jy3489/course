import { test, expect } from '@playwright/test';

// Parameterized: every page must load (HTTP 200) without JavaScript errors.
const pages = ['portal', 'builder', 'admin', 'browse', 'invites', 'stats'];

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

test('app navigation and language controls stay consistent across Portal, Builder and Admin', async ({ page }, testInfo) => {
  for (const name of ['portal', 'builder', 'admin']) {
    await page.goto(`/static/${name}.html`);
    const header = page.locator('header.topbar');
    await expect(header.locator('nav a')).toHaveText(['Portal', 'Builder', 'Admin']);
    await expect(header.locator('nav a[aria-current="page"]')).toHaveAttribute('href', '/' + name);
    await expect(header.getByRole('combobox', { name: 'Language' })).toBeVisible();
    await expect(header.locator('#account-menu')).toBeVisible();
    expect(await header.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await header.screenshot({ path: testInfo.outputPath(`${name}-header.png`) });
  }
  await page.locator('header.topbar nav').getByRole('link', { name: 'Portal', exact: true }).click();
  await expect(page).toHaveURL(/portal(?:\.html)?$/);
});
