import { test, expect } from '@playwright/test';
import { createGame, createUser, loginAs, uniqueName } from './helpers';

test.describe('Choosing and playing a game (logged in, real server)', () => {
  test('search for a game, open it and play a match against the computer', async ({ page, request }) => {
    const owner = await createUser(request, uniqueName('Owner'));
    const gameName = uniqueName('Noughts');
    await createGame(request, owner, gameName);
    const player = await createUser(request, uniqueName('Player'));
    await loginAs(page, player);

    await page.goto('/static/portal.html');
    await page.getByRole('combobox', { name: 'Search games' }).fill(gameName);
    await page.getByRole('option', { name: new RegExp(gameName) }).click();

    await expect(page.locator('#selected-game-name')).toHaveText(gameName);
    // Online computer matches remain server-authoritative.
    await page.getByRole('button', { name: /Create match/ }).click();
    await expect(page.locator('#match')).toBeVisible();
    await page.getByRole('button', { name: 'Add computer opponent' }).click();
    await page.getByRole('button', { name: 'Start game' }).click();

    const board = page.frameLocator('#game-box iframe').locator('#board button');
    await expect(board).toHaveCount(9);
    await board.first().click();
    await expect(board.first()).toHaveText('X');
    // The server moves the computer itself: its O appears without
    // anyone acting on its behalf.
    await expect(board.filter({ hasText: 'O' })).toHaveCount(1, { timeout: 15_000 });
  });

  test('create an online match and see it listed', async ({ page, request }) => {
    const owner = await createUser(request, uniqueName('Owner'));
    const gameName = uniqueName('OnlineGame');
    await createGame(request, owner, gameName);
    await loginAs(page, owner);

    await page.goto('/static/portal.html');
    await page.getByRole('combobox', { name: 'Search games' }).fill(gameName);
    await page.getByRole('option', { name: new RegExp(gameName) }).click();
    await page.getByRole('button', { name: /Create match/ }).click();

    await expect(page.locator('#match')).toBeVisible();
    await expect(page.locator('#match-toolbar-title')).toContainText(gameName);
  });
});
