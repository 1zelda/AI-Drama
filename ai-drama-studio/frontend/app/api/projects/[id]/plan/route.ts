import { NextRequest, NextResponse } from 'next/server';
import { ProjectStore } from '@/lib/store';
import { planDrama } from '@/lib/llm';

export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const project = ProjectStore.get(id);
  if (!project) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  try {
    const plan = await planDrama(project.title, project.description);
    const updated = ProjectStore.updatePlan(id, plan);
    return NextResponse.json(updated);
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
