// React DevTools coexistence (spike 4). bippy installs __REACT_DEVTOOLS_GLOBAL_HOOK__ and hides
// it from only the FIRST `hasOwnProperty` check, but DevTools 8 checks twice, so DevTools would
// bail out and never install its own hook. Keep answering "no" while the hook is still bippy's;
// once DevTools replaces it, defer to the native method.
const HOOK_KEY = '__REACT_DEVTOOLS_GLOBAL_HOOK__';
const nativeHasOwn = Object.prototype.hasOwnProperty;

export function installDevtoolsShim(win: Window & typeof globalThis = window): void {
  const shim = function (this: unknown, key: PropertyKey): boolean {
    if (key === HOOK_KEY && this === win) {
      const h = (win as unknown as Record<string, { _isBippyHook?: boolean } | undefined>)[HOOK_KEY];
      if (h?._isBippyHook === true) return false;
    }
    return nativeHasOwn.call(this, key);
  };
  Object.defineProperty(win, 'hasOwnProperty', { configurable: true, writable: true, value: shim });
}
