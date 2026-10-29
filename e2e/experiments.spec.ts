import { test, expect } from '@playwright/test';
import { createUser, loginAs, uniqueName } from './helpers';

test('skin assignment is consistent across the portal and builder', async ({ page, request }) => {
  const user = await createUser(request, uniqueName('Skin'));
  await loginAs(page, user);

  await page.goto('/static/portal.html');
  const root = page.locator('html');
  await expect(root).toHaveAttribute('data-skin', /^(classic|aurora|sunset)$/);
  const skin = await root.getAttribute('data-skin');

  await page.goto('/static/builder.html');
  await expect(root).toHaveAttribute('data-skin', skin!);
});

test('campaign link redirects to the portal and remembers its code', async ({ page }) => {
  await page.goto('/c/students-a');
  await expect(page).toHaveURL(/\/static\/portal\.html\?campaign=students-a$/);
  await expect
    .poll(() =>
      page.evaluate(() =>
        localStorage.getItem('game-platform.experiment-campaign.v1'),
      ),
    )
    .toBe('students-a');
});
