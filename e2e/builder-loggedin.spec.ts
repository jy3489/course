import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { createUser, loginAs, uniqueName } from './helpers';

async function openBuilder(page: Page, request: Parameters<typeof createUser>[0]) {
  await loginAs(page, await createUser(request, uniqueName('Builder')));
  await page.goto('/static/builder.html');
  await expect(page.getByRole('heading', { name: 'Game Builder' })).toBeVisible();
  await page.getByRole('button', { name: 'Create a game', exact: true }).click();
}

test.describe('Builder (logged in)', () => {
  test('shows the workspace and lets you start a new game with a prompt', async ({ page, request }) => {
    await openBuilder(page, request);
    await expect(page.locator('#welcome')).toBeHidden();
    await expect(page.getByRole('heading', { name: 'Create a game' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create game' })).toBeVisible();
    await page.getByPlaceholder('Describe the game you want to play…').fill('A game of tic-tac-toe');
    await expect(page.getByPlaceholder('Describe the game you want to play…')).toHaveValue('A game of tic-tac-toe');
  });

  test('"Use as starting point" is disabled when there are no starter games', async ({ page, request }) => {
    // Other tests publish games on the shared server, so mock an empty catalog.
    await page.route('**/games', (route) => route.fulfill({ json: [] }));
    await openBuilder(page, request);
    await expect(page.getByRole('button', { name: 'Use as starting point' })).toBeDisabled();
  });

  test('stays usable when the AI request fails (mocked)', async ({ page, request }) => {
    await page.route('**/agent**', (route) => route.fulfill({ status: 500, json: { detail: 'model down' } }));
    await openBuilder(page, request);
    await page.getByPlaceholder('Describe the game you want to play…').fill('Make me a game');
    await page.getByRole('button', { name: 'Create game' }).click();
    await expect(page.locator('#agent-message, #agent-log').filter({ hasText: /\S/ }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create game' })).toBeEnabled();
  });
});
