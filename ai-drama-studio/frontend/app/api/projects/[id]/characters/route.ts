import { NextRequest, NextResponse } from 'next/server';
import { ProjectStore } from '@/lib/store';
import { CharacterManager } from '@/lib/character_manager';

const STORAGE_DIR = 'data';

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cm = new CharacterManager(id, STORAGE_DIR);
  return NextResponse.json(cm.list());
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { name, role, description, appearance, costume, personality } = await req.json();
  const cm = new CharacterManager(id, STORAGE_DIR);
  const char = cm.add(name, role, description, appearance, costume, personality || '');
  const project = ProjectStore.get(id);
  if (project) {
    project.characters = cm.list().map(c => ({ name: c.name, role: c.role, appearance: c.appearance }));
    ProjectStore.update(id, { characters: project.characters });
  }
  return NextResponse.json(char);
}
