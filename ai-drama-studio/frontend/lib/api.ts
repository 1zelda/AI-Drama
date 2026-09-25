/** Proxy client to the FastAPI backend (single source of truth). */
const BACKEND_URL = process.env.BACKEND_URL || 'http://127.0.0.1:8000';

export async function backendFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    cache: 'no-store',
    signal: AbortSignal.timeout(300_000),
  }).catch((err) => {
    return {
      status: 502,
      ok: false,
      json: async () => ({ detail: `后端不可达 (${BACKEND_URL}): ${err?.message || err}` }),
      text: async () => `后端不可达 (${BACKEND_URL})`,
    } as Response;
  });
  return res;
}

export async function passthrough(path: string, init?: RequestInit) {
  const res = await backendFetch(path, init);
  const text = await res.text();
  let body: unknown;
  try { body = JSON.parse(text); } catch { body = { raw: text }; }
  return Response.json(body, { status: res.status });
}

/** Encode a backend-relative asset URL (may contain CJK) for safe proxying. */
export function encodeAssetUrl(url: string): string {
  return url.split('/').map(encodeURIComponent).join('/');
}

export { BACKEND_URL };
