import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  workers: 1,
  retries: 0,
  use: { browserName: 'chromium', screenshot: 'only-on-failure', trace: 'off' },
});
