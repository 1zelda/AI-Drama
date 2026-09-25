import { NextRequest } from "next/server";
import { forward } from "@/lib/backend";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return forward(`/api/projects/${id}/characters`);
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  return forward(`/api/projects/${id}/characters`, { method: "POST", body });
}
