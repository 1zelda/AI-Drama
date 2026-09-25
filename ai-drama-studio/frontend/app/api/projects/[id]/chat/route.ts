import { NextRequest, NextResponse } from 'next/server';
import { ProjectStore } from '@/lib/store';
import { chatWithPlanner } from '@/lib/llm';

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { message } = await req.json();
  const project = ProjectStore.get(id);
  if (!project) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  try {
    const result = await chatWithPlanner(project, message);
    return NextResponse.json(result);
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
