import { NextRequest } from 'next/server';
import { passthrough } from '@/lib/api';

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string; item: string }> }) {
  const { id, item } = await params;
  const body = await req.json();
  return passthrough(`/api/projects/${id}/approvals/${item}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
