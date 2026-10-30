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

test('experiment dashboard explains creation and supports deleting variants', async ({
  page,
  request,
}) => {
  const skinName = uniqueName('Temporary skin');
  const skinKey = uniqueName('temporary-skin')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .slice(0, 50);
  const campaignName = uniqueName('Temporary campaign');
  const campaignKey = uniqueName('temporary-campaign')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .slice(0, 50);
  const skinResponse = await request.post('/admin/experiments/skin-variants', {
    data: { name: skinName, key: skinKey },
  });
  expect(skinResponse.status()).toBe(201);
  const campaignResponse = await request.post(
    '/admin/experiments/campaign-links',
    { data: { name: campaignName, key: campaignKey } },
  );
  expect(campaignResponse.status()).toBe(201);

  await page.goto('/static/stats.html');
  const skinPanel = page
    .locator('.experiment-panel')
    .filter({ hasText: 'UI skin experiment' });
  const campaignPanel = page
    .locator('.experiment-panel')
    .filter({ hasText: 'Advertisement campaigns' });
  await expect(skinPanel).toContainText(skinName);
  await expect(skinPanel).not.toContainText(/cost|budget/i);
  await expect(campaignPanel).toContainText(campaignName);
  await expect(campaignPanel).not.toContainText(/actual spend|cost/i);

  await page
    .getByText('Step-by-step: create and use a campaign link')
    .click();
  await expect(page.getByText(/Copy the generated link/)).toBeVisible();
  await page.getByText('Step-by-step: prepare and add a UI skin').click();
  await expect(
    page.getByText(/does not create, upload, or deploy the design/),
  ).toBeVisible();

  page.once('dialog', (dialog) => dialog.accept());
  await skinPanel.getByRole('button', { name: `Delete ${skinName}` }).click();
  await expect(skinPanel.getByText(skinName)).toHaveCount(0);

  page.once('dialog', (dialog) => dialog.accept());
  await campaignPanel
    .getByRole('button', { name: `Delete ${campaignName}` })
    .click();
  await expect(campaignPanel.getByText(campaignName)).toHaveCount(0);
});
