import { NextRequest } from 'next/server';
import { passthrough } from '@/lib/api';

export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return passthrough(`/api/projects/${id}/plan`, { method: 'POST' });
}
