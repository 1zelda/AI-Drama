import { NextResponse } from 'next/server';
import { loadSettings, saveSettings } from '@/lib/settings';

export async function GET() {
  const s = loadSettings();
  try {
    const res = await fetch(s.comfyui_url.replace(/\/$/, '') + '/system_stats', { signal: AbortSignal.timeout(5000) });
    const available = res.ok;
    saveSettings({ ...s, comfyui_available: available });
    return NextResponse.json({ available, url: s.comfyui_url });
  } catch {
    saveSettings({ ...s, comfyui_available: false });
    return NextResponse.json({ available: false, url: s.comfyui_url });
  }
}
