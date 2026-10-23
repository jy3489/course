import { test, expect } from '@playwright/test';
import { createGame, createUser, loginAs, uniqueName } from './helpers';

test.describe('Choosing and playing a game (logged in, real server)', () => {
  test('search for a game, open it and play a practice match against the computer', async ({ page, request }) => {
    const owner = await createUser(request, uniqueName('Owner'));
    const gameName = uniqueName('Noughts');
    await createGame(request, owner, gameName);
    const player = await createUser(request, uniqueName('Player'));
    await loginAs(page, player);

    await page.goto('/static/portal.html');
    await page.getByRole('combobox', { name: 'Search games' }).fill(gameName);
    await page.getByRole('option', { name: new RegExp(gameName) }).click();

    await expect(page.locator('#selected-game-name')).toHaveText(gameName);
    await page.locator('#practice-mode').selectOption('computer');
    await page.getByRole('button', { name: /Play on this device/ }).click();

    const board = page.frameLocator('#game-box iframe').locator('#board button');
    await expect(board).toHaveCount(9);
    await board.first().click();
    await expect(board.first()).toHaveText('X');
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
