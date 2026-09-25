import { NextRequest, NextResponse } from 'next/server';
import { loadSettings, saveSettings } from '@/lib/settings';

export async function GET() {
  return NextResponse.json(loadSettings());
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  saveSettings(body);
  return NextResponse.json({ ok: true });
}
