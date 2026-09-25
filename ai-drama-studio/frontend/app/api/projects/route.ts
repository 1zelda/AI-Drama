import { NextRequest } from 'next/server';
import { passthrough } from '@/lib/api';

export async function GET() {
  return passthrough('/api/projects/');
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  return passthrough('/api/projects/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
