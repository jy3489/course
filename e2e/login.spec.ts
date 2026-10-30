import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { uniqueName } from './helpers';

// "Log in" is a menu button; it opens a panel whose own "Log in" button opens the dialog.
async function openLoginDialog(page: Page) {
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await page.locator('.auth-menu-panel').getByRole('button', { name: 'Log in' }).click();
}

test.describe('Guest login (real server)', () => {
  test('an open login menu keeps keyboard focus across portal polling', async ({page}) => {
    await page.goto('/static/portal.html');
    await page.getByRole('button', {name:'Log in', exact:true}).click();
    const login=page.locator('.auth-menu-panel').getByRole('button', {name:'Log in', exact:true});
    await login.focus();
    await expect(login).toBeFocused();
    await page.waitForResponse(response => new URL(response.url()).pathname==='/games' && response.request().method()==='GET');
    await expect(login).toBeFocused();
    await login.click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    for(let i=0;i<2;i++) {
      await page.getByRole('button',{name:'Log in',exact:true}).click();
      await expect(page.locator('.auth-menu-panel')).toBeVisible();
      await page.getByRole('heading',{name:'Game platform',exact:true}).click();
      await expect(page.locator('.auth-menu-panel')).toBeHidden();
    }
  });
  test('log in as a guest, see the name in the menu, then log out', async ({ page }) => {
    const name = uniqueName('Guest');
    await page.goto('/static/portal.html');
    await openLoginDialog(page);

    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'Log in' })).toBeVisible();
    await dialog.getByLabel('Guest name').fill(name);
    await dialog.getByRole('button', { name: 'Continue as guest' }).click();

    await expect(dialog).toBeHidden();
    const menu = page.getByRole('button', { name, exact: true });
    await expect(menu).toBeVisible();

    await menu.click();
    await page.evaluate(() =>
      document.dispatchEvent(new Event('i18n-changed')),
    );
    await expect(page.getByText(`Logged in as ${name}`)).toBeVisible();
    await page.getByRole('button', { name: `Log out ${name}` }).click();
    await expect(page.getByRole('button', { name: 'Log in', exact: true })).toBeVisible();
  });

  test('the login survives a page reload', async ({ page }) => {
    const name = uniqueName('Persist');
    await page.goto('/static/portal.html');
    await openLoginDialog(page);
    await page.getByLabel('Guest name').fill(name);
    await page.getByRole('button', { name: 'Continue as guest' }).click();
    await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
  });

  test('the dialog closes with the × button', async ({ page }) => {
    await page.goto('/static/portal.html');
    await openLoginDialog(page);
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('button', { name: 'Close' }).click();
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test('shows an error in the dialog when account creation fails', async ({ page }) => {
    await page.route('**/users', (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({ status: 500, json: { detail: 'server exploded' } })
        : route.continue(),
    );
    await page.goto('/static/portal.html');
    await openLoginDialog(page);
    await page.getByRole('button', { name: 'Continue as guest' }).click();
    await expect(page.getByRole('dialog').locator('.message.error')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Log in', exact: true })).toBeVisible();
  });
});
