// FastAPI backend base. Override with NEXT_PUBLIC_API_BASE in .env.local
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export function sseUrl(runId: string, lastSeq = 0): string {
  return `${API_BASE}/api/runs/${runId}/events?last_seq=${lastSeq}`;
}
