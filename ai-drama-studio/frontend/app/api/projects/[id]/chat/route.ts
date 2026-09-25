import { NextRequest } from "next/server";
import { forward } from "@/lib/backend";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  return forward(`/api/projects/${id}/chat`, {
    method: "POST",
    body: { message: body.message || "", context: body.context },
  });
}
