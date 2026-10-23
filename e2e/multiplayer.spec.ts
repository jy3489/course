import { test, expect } from '@playwright/test';
import { createGame, createUser, loginAs, uniqueName } from './helpers';

test('two players in separate browsers: join an online match and see each other\'s moves', async ({ browser, request }) => {
  const alice = await createUser(request, uniqueName('Alice'));
  const bob = await createUser(request, uniqueName('Bob'));
  const gameId = await createGame(request, alice, uniqueName('Duel'));
  const auth = (u: typeof alice) => ({ 'X-User-Id': u.id, 'X-User-Password': u.password });

  const created = await request.post('/matches', { headers: auth(alice), data: { game_id: gameId, num_computer_opponents: 0 } });
  expect(created.ok()).toBeTruthy();
  const match = await created.json();
  expect((await request.post(`/matches/${match.id}/join`, { headers: auth(bob) })).ok()).toBeTruthy();
  expect((await request.post(`/matches/${match.id}/start`, { headers: auth(alice), data: {} })).ok()).toBeTruthy();

  const open = async (user: typeof alice) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await loginAs(page, user);
    await page.goto(`/static/portal.html#match=${match.id}`);
    return { context, page, board: page.frameLocator('#game-box iframe').locator('#board button') };
  };
  const a = await open(alice);
  const b = await open(bob);

  await expect(a.board).toHaveCount(9);
  await expect(b.board).toHaveCount(9);

  await a.board.nth(4).click();
  await expect(a.board.nth(4)).toHaveText(/X|O/);
  // Bob's page polls the server, so allow it time to notice Alice's move.
  await expect(b.board.nth(4)).toHaveText(/X|O/, { timeout: 15_000 });

  await a.context.close();
  await b.context.close();
});
