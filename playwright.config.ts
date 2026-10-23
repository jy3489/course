import fs from 'node:fs';
import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

const port = process.env.E2E_PORT ?? '8123';
// Uses E2E_PYTHON if set, else ./.venv (create it: see README), else python3 (CI).
const venvPython = path.resolve(__dirname, '.venv/bin/python');
const python = process.env.E2E_PYTHON ?? (fs.existsSync(venvPython) ? venvPython : 'python3');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'html',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'on-first-retry',
    // SLOWMO=800 delays each action (ms) so you can watch it with --headed.
    launchOptions: { slowMo: Number(process.env.SLOWMO ?? 0) },
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    // responsive.spec.ts sets its own viewports, so the phone project skips it.
    { name: 'mobile', use: { ...devices['Pixel 7'] }, testIgnore: /responsive/ },
  ],
  webServer: {
    command: `"${python}" e2e/start-server.py`,
    url: `http://127.0.0.1:${port}/static/portal.html`,
    reuseExistingServer: !process.env.CI,
    env: { E2E_PORT: port },
  },
});
