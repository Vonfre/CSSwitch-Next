import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  workers: 1,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:4178', headless: true, viewport: { width: 980, height: 720 }, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) },
  webServer: { command: 'node scripts/preview.mjs', url: 'http://127.0.0.1:4178', reuseExistingServer: false },
});
