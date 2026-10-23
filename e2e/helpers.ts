import fs from 'node:fs';
import path from 'node:path';
import { expect, type APIRequestContext, type Page } from '@playwright/test';

export const TICTACTOE = fs.readFileSync(path.resolve(__dirname, '../games/tictactoe.html'), 'utf8');

export interface Account { id: string; display_name: string; password: string }

/** Creates a real guest user on the test server (the same call the Log in dialog makes). */
export async function createUser(request: APIRequestContext, name: string): Promise<Account> {
  const res = await request.post('/users', { data: { display_name: name } });
  expect(res.ok()).toBeTruthy();
  return res.json();
}

/** Publishes a real tic-tac-toe game owned by [owner]. Returns its id. */
export async function createGame(request: APIRequestContext, owner: Account, name: string): Promise<string> {
  const res = await request.post('/games', {
    headers: { 'X-User-Id': owner.id, 'X-User-Password': owner.password },
    data: { name, description: 'UI test game', allowed_player_counts: [2], code: TICTACTOE },
  });
  expect(res.ok(), await res.text()).toBeTruthy();
  return (await res.json()).id;
}

/** Pretends the browser already logged in as [user], as the site itself stores it. */
export async function loginAs(page: Page, user: Account) {
  const stored = { ...user, linked: [], picture_url: '' };
  await page.addInitScript((u) => {
    if (window !== window.top) return;
    localStorage.setItem('game-platform.accounts.v1', JSON.stringify([u]));
    localStorage.setItem('game-platform.current-account.v1', u.id);
  }, stored);
}

export const uniqueName = (prefix: string) => `${prefix} ${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
