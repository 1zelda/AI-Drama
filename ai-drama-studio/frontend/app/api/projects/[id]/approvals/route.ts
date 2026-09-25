import { forward } from "@/lib/backend";

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const stage = new URL(req.url).searchParams.get("stage");
  return forward(`/api/projects/${id}/approvals${stage ? `?stage=${encodeURIComponent(stage)}` : ""}`);
}
