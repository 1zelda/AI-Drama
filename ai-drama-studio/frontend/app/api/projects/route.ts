import { NextRequest } from "next/server";
import { forward } from "@/lib/backend";

export async function GET() {
  return forward("/api/projects/");
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  if (!body?.title) {
    return Response.json({ error: "缺少标题" }, { status: 400 });
  }
  return forward("/api/projects/", {
    method: "POST",
    body: { title: body.title, description: body.description || "", genre: body.genre || "" },
  });
}
