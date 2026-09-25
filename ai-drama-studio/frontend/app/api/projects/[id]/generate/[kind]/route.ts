import { NextRequest } from 'next/server';
import { passthrough } from '@/lib/api';

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; kind: string }> },
) {
  const { id, kind } = await params;
  return passthrough(`/api/projects/${id}/generate/${kind}`, { method: 'POST' });
}
