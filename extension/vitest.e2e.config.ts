import { defineConfig } from 'vitest/config';

// Playwright smoke test: loads .output/chrome-mv3 in headless Chromium against a fake daemon.
export default defineConfig({
  test: {
    include: ['tests/e2e/**/*.test.ts'],
    environment: 'node',
    testTimeout: 60_000,
    hookTimeout: 60_000,
    fileParallelism: false,
  },
});
