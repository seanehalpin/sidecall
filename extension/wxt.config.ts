import { fileURLToPath } from 'node:url';
import { defineConfig } from 'wxt';

// Pinned public key: keeps the extension ID stable across unpacked installs, so
// the daemon's Origin check (SPEC §6.1, §9.1) has a fixed value.
// ID: kcjkimilmanlonlplfancfpoekkphjhb
export const EXTENSION_KEY =
  'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkCSVEB0+Bn+zAj4YYPl5Dm+SF02Z+NlCg6zHU1cYkli+hz/NZ6+MA3SBaOul3UUaS88ngolzYVlpWH0JvEcLnEB9W15ZvUxpmtvLGzGNLSH6xT8ImLHGcgKFqCsxQIm4lLAt6PSCBgyVQyuykKhOAf0ifMsjumGPeRKt8KMyQ69qWXObDeFFMY9iaa4kW11umx9vgMnfZ3ge8wLxqKWxm5/GteYGxb048JxHaD21+jcb/9nyXBhIhmgSE25Rxygku/JNKCbkq5bWk71ZktzgvmlEiX9f9Yu5IAbzFSdqe+Bk2m5iLUA3gGKHoZN1UTsBE2uZF1yGpOiOFC3lMD0jbwIDAQAB';

export default defineConfig({
  srcDir: 'src',
  modules: ['@wxt-dev/module-svelte'],
  // `pnpm dev` only builds and watches; load the output into your own Chrome.
  webExt: { disabled: true },
  vite: () => ({
    resolve: {
      alias: {
        // bippy's root entry imports `react` for a hook the picker never uses; keep React out
        // of the content-script bundle.
        react: fileURLToPath(new URL('./src/lib/picker/stubs/react-stub.js', import.meta.url)),
      },
    },
  }),
  manifest: {
    name: 'Sidecall',
    description: 'Prototype by conversation: point, talk, and let Claude Code edit your local app.',
    key: EXTENSION_KEY,
    // sidePanel is added automatically for the sidepanel entrypoint; listed for clarity.
    permissions: ['sidePanel', 'storage', 'notifications', 'alarms', 'tabs'],
    // <all_urls> is what chrome.tabs.captureVisibleTab needs for the page-context
    // screenshot on every turn (activeTab only lasts until the next navigation).
    host_permissions: ['http://localhost/*', 'http://127.0.0.1/*', '<all_urls>'],
    action: { default_title: 'Open Sidecall' },
  },
});
