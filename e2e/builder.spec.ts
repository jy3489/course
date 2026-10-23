import { test, expect } from '@playwright/test';

test.describe('Builder', () => {
  test('shows the welcome screen with a sign-in button when logged out', async ({ page }) => {
    await page.goto('/static/builder.html');
    await expect(page).toHaveTitle(/Builder/);
    await expect(page.getByRole('heading', { name: 'Game Builder' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
    await expect(page.locator('#workspace')).toBeHidden();
  });
});
