import { NextResponse } from 'next/server';
import * as fs from 'fs';
import * as path from 'path';

const CONFIG_DIR = path.join(process.cwd(), '..', 'backend', 'config', 'comfyui_workflows');

export async function GET() {
  const workflows: any[] = [];
  if (fs.existsSync(CONFIG_DIR)) {
    for (const fp of fs.readdirSync(CONFIG_DIR).filter(f => f.endsWith('.json'))) {
      const wf = JSON.parse(fs.readFileSync(path.join(CONFIG_DIR, fp), 'utf-8'));
      workflows.push({ name: wf.name || fp.replace('.json', ''), description: wf.description || '', type: wf.type || 'image', output_node: wf.output_node || '9' });
    }
  }
  return NextResponse.json(workflows);
}
