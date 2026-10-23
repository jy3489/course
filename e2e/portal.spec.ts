import { test, expect } from '@playwright/test';

test.describe('Portal', () => {
  test('loads with the right title and heading', async ({ page }) => {
    await page.goto('/static/portal.html');
    await expect(page).toHaveTitle(/Portal/);
    await expect(page.getByRole('heading', { name: 'Game platform' })).toBeVisible();
  });

  test('offers a Log in button to logged-out visitors', async ({ page }) => {
    await page.goto('/static/portal.html');
    await expect(page.getByRole('button', { name: 'Log in' })).toBeVisible();
  });

  test('match groups start empty', async ({ page }) => {
    await page.goto('/static/portal.html');
    for (const id of ['turn', 'ongoing', 'waiting', 'over']) {
      await expect(page.locator(`#count-${id}`)).toHaveText('0');
    }
  });

  test('shows an error-free page when the games API fails', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('**/games*', (route) =>
      route.fulfill({ status: 500, json: { detail: 'boom' } }),
    );
    await page.goto('/static/portal.html');
    await expect(page.getByRole('heading', { name: 'Game platform' })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('lists a mocked game in the library', async ({ page }) => {
    await page.route('**/games', (route) =>
      route.fulfill({
        json: [
          {
            id: 'g1', name: 'Mock Chess', version: 1, description: 'A mocked game',
            allowed_player_counts: [2], published: true, owner_user_id: 'u1',
          },
        ],
      }),
    );
    await page.goto('/static/portal.html');
    await page.getByRole('button', { name: 'Browse all games' }).click();
    await expect(page.getByText('Mock Chess').first()).toBeVisible();
  });
});
