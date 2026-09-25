import { NextRequest } from 'next/server';
import { passthrough } from '@/lib/api';

/** POST /api/projects/{id}/generate/{kind}[/{ep}] -> backend generation endpoints. */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; kind: string; ep?: string }> },
) {
  const { id, kind, ep } = await params;
  const path = ep ? `/api/projects/${id}/generate/${kind}/${ep}` : `/api/projects/${id}/generate/${kind}`;
  return passthrough(path, { method: 'POST' });
}
