import { NextRequest } from 'next/server';
import { backendFetch } from '@/lib/api';

/** Proxy generated asset files (character sheets, storyboard images) from the backend. */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  const encoded = path.map(encodeURIComponent).join('/');
  const range = req.headers.get('range');
  const upstream = await backendFetch(`/assets/${encoded}`, {
    headers: range ? { Range: range } : undefined,
  });
  const body = await upstream.arrayBuffer();
  return new Response(body, {
    status: upstream.status,
    headers: {
      'Content-Type': upstream.headers.get('content-type') || 'application/octet-stream',
      'Cache-Control': 'no-store',
    },
  });
}
