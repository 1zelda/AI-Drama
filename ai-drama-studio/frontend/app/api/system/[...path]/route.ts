import { forward } from "@/lib/backend";

export const dynamic = "force-dynamic";

/**
 * /api/system/* → FastAPI /api/system/*。
 * 自检里 LLM 真发一次请求、生图实调会慢，超时给足。
 */
export async function GET(
  req: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  const search = new URL(req.url).search || "";
  return forward(`/api/system/${path.join("/")}${search}`, { timeoutMs: 300_000 });
}
