import { defineConfig } from 'vitest/config';

// Unit tests cover the pure logic in src/lib; the Playwright smoke test has its own config.
export default defineConfig({
  test: {
    include: ['tests/unit/**/*.test.ts'],
    environment: 'node',
  },
});
