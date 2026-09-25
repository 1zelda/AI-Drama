import { forward } from "@/lib/backend";

export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return forward(`/api/projects/${id}/plan`, { method: "POST", body: {} });
}
