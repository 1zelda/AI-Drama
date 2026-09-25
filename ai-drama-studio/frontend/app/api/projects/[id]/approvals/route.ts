import { NextRequest } from 'next/server';
import { passthrough } from '@/lib/api';

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const stage = req.nextUrl.searchParams.get('stage');
  return passthrough(`/api/projects/${id}/approvals${stage ? `?stage=${stage}` : ''}`);
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json();
  return passthrough(`/api/projects/${id}/approvals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
