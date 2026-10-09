// Which tab origins can bind to a repo (SPEC §9.1): only http://localhost:* and
// http://127.0.0.1:*. Everything else is reported to the daemon as null.

export function allowedOrigin(url: string | null | undefined): string | null {
  if (!url) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== 'http:') return null;
  if (parsed.hostname !== 'localhost' && parsed.hostname !== '127.0.0.1') return null;
  if (parsed.username || parsed.password) return null;
  return parsed.origin;
}
