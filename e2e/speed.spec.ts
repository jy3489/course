import { test, expect } from '@playwright/test';
import { TICTACTOE, createUser, loginAs, uniqueName } from './helpers';

// A real 1-minute speed match: Alice never moves, the server's clock runs out, and a
// computer takes over her seat while Bob plays on. It waits the real 60 seconds (the
// server has no test clock), so it runs once, in the desktop project only.
test('a 1-minute speed match: the player who doesn\'t move times out and a computer takes over', async ({ browser, request }, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', 'waits a real minute; once is enough');
  test.setTimeout(150_000);
  const alice = await createUser(request, uniqueName('Alice'));
  const bob = await createUser(request, uniqueName('Bob'));
  const auth = (u: typeof alice) => ({ 'X-User-Id': u.id, 'X-User-Password': u.password });
  const game = await request.post('/games', {
    headers: auth(alice),
    data: { name: uniqueName('Speed duel'), description: 'UI test game', allowed_player_counts: [2], allows_leave_mid_match: true, code: TICTACTOE },
  });
  expect(game.ok(), await game.text()).toBeTruthy();
  const created = await request.post('/matches', { headers: auth(alice), data: { game_id: (await game.json()).id, turn_seconds: 60 } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const match = await created.json();
  expect(match.turn_seconds).toBe(60);
  expect((await request.post(`/matches/${match.id}/join`, { headers: auth(bob) })).ok()).toBeTruthy();
  expect((await request.post(`/matches/${match.id}/start`, { headers: auth(alice), data: {} })).ok()).toBeTruthy();

  const open = async (user: typeof alice) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await loginAs(page, user);
    await page.goto(`/static/portal.html#match=${match.id}`);
    return { context, page, clock: page.locator('#match-clock'), notice: page.locator('#match-play-notice') };
  };
  const a = await open(alice);
  const b = await open(bob);

  // Both see the running clock: Alice as her own move, Bob as Alice's.
  await expect(a.clock).toHaveText(/^Your move: 0:5\d$/);
  await expect(a.clock).not.toHaveClass(/urgent/);
  await expect(b.clock).toHaveText(new RegExp(`^${alice.display_name}: 0:5\\d$`));

  // In the last 10 seconds the clock turns red for both of them.
  await expect(a.clock).toHaveText(/^Your move: 0:(0\d|10)$/, { timeout: 65_000 });
  await expect(a.clock).toHaveClass(/urgent/);
  await expect(b.clock).toHaveClass(/urgent/);

  // Time's up: Alice is told, and stays as a viewer; Bob sees who was replaced.
  await expect(a.notice).toContainText('You ran out of time. A computer is playing for you now.', { timeout: 20_000 });
  await expect(b.notice).toContainText(`Time ran out: a computer took over for ${alice.display_name}`, { timeout: 20_000 });
  const after = await (await request.get(`/matches/${match.id}`)).json();
  expect(after.players[0].kind).toBe('computer');
  expect(after.timeouts.map((t: { user_id: string }) => t.user_id)).toEqual([alice.id]);
  expect(after.status).toBe('ongoing');

  // The match goes on: Bob's browser plays the computer's move for seat 0.
  await expect.poll(async () => (await (await request.get(`/matches/${match.id}`)).json()).move_count, { timeout: 20_000 }).toBeGreaterThan(0);

  await a.context.close();
  await b.context.close();
});
