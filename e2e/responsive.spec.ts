import { test, expect } from '@playwright/test';
import { createGame, createUser, loginAs, uniqueName } from './helpers';

const sizes = [
  { name: 'small phone portrait', width: 360, height: 640 },
  { name: 'phone portrait', width: 390, height: 844 },
  { name: 'phone landscape', width: 844, height: 390 },
  { name: 'tablet portrait', width: 820, height: 1180 },
  { name: 'laptop', width: 1366, height: 768 },
  { name: 'wide desktop', width: 1920, height: 1080 },
];
const pages = ['portal', 'builder', 'browse'];

test.describe('Layout at different screen sizes', () => {
  for (const size of sizes) {
    for (const name of pages) {
      test(`${name}: no horizontal scrolling on ${size.name} (${size.width}x${size.height})`, async ({ page }) => {
        await page.setViewportSize({ width: size.width, height: size.height });
        await page.goto(`/static/${name}.html`);
        await expect(page.getByRole('heading', { name: /Game platform|Game Builder/ }).first()).toBeVisible();
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow).toBeLessThanOrEqual(0);
      });
    }
  }

  // The sidebar is a slide-in drawer at every screen size.
  for (const size of [sizes[1], sizes[4]]) {
    test(`portal: the sidebar drawer opens and closes on ${size.name}`, async ({ page, request }) => {
      await loginAs(page, await createUser(request, uniqueName('Sidebar')));
      await page.setViewportSize({ width: size.width, height: size.height });
      await page.goto('/static/portal.html');
      const sidebar = page.locator('#app-sidebar');
      const toggle = page.getByRole('button', { name: 'Open sidebar' });

      await expect(sidebar).toBeHidden();
      await toggle.click();
      await expect(sidebar).toBeVisible();
      await expect(sidebar.getByRole('button', { name: /New match/ })).toBeVisible();
      await sidebar.getByRole('button', { name: /Close sidebar/ }).click();
      await expect(sidebar).toBeHidden();
    });
  }

  test('match page: the game frame stays usable when the screen is rotated', async ({ page, request }) => {
    const owner = await createUser(request, uniqueName('Rotate'));
    const gameName = uniqueName('RotateGame');
    await createGame(request, owner, gameName);
    await loginAs(page, owner);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/static/portal.html');
    await page.getByRole('combobox', { name: 'Search games' }).fill(gameName);
    await page.getByRole('option', { name: new RegExp(gameName) }).click();
    await page.getByRole('button', { name: /Play on this device/ }).click();

    const frame = page.locator('#game-box iframe');
    const board = page.frameLocator('#game-box iframe').locator('#board button');
    await expect(board).toHaveCount(9);
    await board.first().click();
    await expect(board.first()).toHaveText('X');

    for (const [w, h] of [[844, 390], [390, 844], [1280, 800]]) {
      await page.setViewportSize({ width: w, height: h });
      await expect(frame).toBeVisible();
      const box = await page.locator('#game-box').boundingBox();
      expect(box!.width).toBeGreaterThan(250);
      expect(box!.width).toBeLessThanOrEqual(w);
      // The move already made is kept; the page is not reloaded.
      await expect(board.first()).toHaveText('X');
    }
  });
});
