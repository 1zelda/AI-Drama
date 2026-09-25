import { NextRequest } from 'next/server';
import { passthrough } from '@/lib/api';

/**
 * Frontend page posts { workflow, input } to /api/workflows/execute.
 * Backend expects POST /api/workflows/{name}/execute with { workflow_name, input_data }.
 */
export async function POST(req: NextRequest) {
  const body = await req.json();
  const name = body.workflow || body.workflow_name;
  if (!name) {
    return Response.json({ detail: '缺少 workflow 名称' }, { status: 400 });
  }
  return passthrough(`/api/workflows/${name}/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workflow_name: name, input_data: body.input || body.input_data || {} }),
  });
}
