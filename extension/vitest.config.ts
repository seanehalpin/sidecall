import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Unit tests cover the pure logic in src/lib; the Playwright smoke test has its own config.
// DOM-touching picker tests opt in to happy-dom with a `@vitest-environment` docblock.
export default defineConfig({
  resolve: {
    alias: {
      // Same stub as wxt.config.ts: bippy's root entry imports `react`, which we don't ship.
      react: fileURLToPath(new URL('./src/lib/picker/stubs/react-stub.js', import.meta.url)),
    },
  },
  test: {
    include: ['tests/unit/**/*.test.ts'],
    environment: 'node',
  },
});
