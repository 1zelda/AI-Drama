import { NextRequest, NextResponse } from 'next/server';
import { ProjectStore } from '@/lib/store';

export async function GET() {
  const projects = ProjectStore.list();
  return NextResponse.json(projects);
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  const project = ProjectStore.create(body.title, body.description || '');
  return NextResponse.json(project);
}
