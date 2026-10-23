import base from './playwright.config';
import { defineConfig } from '@playwright/test';

// Separate config so the audit (which reports problems rather than failing) is not part of the normal test run.
export default defineConfig({
  ...base,
  testDir: './audit',
  projects: [{ name: 'audit' }],
  reporter: 'list',
  retries: 0,
  timeout: 300_000,
});
