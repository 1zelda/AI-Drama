import { NextRequest, NextResponse } from 'next/server';
import { ApprovalGate } from '@/lib/approval';

const STORAGE_DIR = 'data';

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const gate = new ApprovalGate(id, STORAGE_DIR);
  const url = new URL(req.url);
  const stage = url.searchParams.get('stage');
  if (stage) return NextResponse.json(gate.getItems(stage));
  return NextResponse.json(gate.getItems());
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { action, feedback, stage, itemType, itemId, title, description } = await req.json();
  const gate = new ApprovalGate(id, STORAGE_DIR);
  if (action === 'create') {
    const item = gate.create(stage, itemType, itemId, title, description || '');
    return NextResponse.json(item);
  }
  return NextResponse.json({ error: 'Invalid action' }, { status: 400 });
}
