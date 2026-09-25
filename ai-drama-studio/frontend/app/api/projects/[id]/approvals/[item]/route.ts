import { NextResponse } from 'next/server';
import { ApprovalGate } from '@/lib/approval';

const STORAGE_DIR = 'data';

export async function POST(req: Request, { params }: { params: Promise<{ id: string; item: string }> }) {
  const { id, item } = await params;
  const { action, feedback } = await req.json();
  const gate = new ApprovalGate(id, STORAGE_DIR);
  let result;
  if (action === 'approve') result = gate.approve(item);
  else if (action === 'reject') result = gate.reject(item, feedback || '');
  else return NextResponse.json({ error: 'Unknown action' }, { status: 400 });
  if (!result) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  return NextResponse.json(result);
}
