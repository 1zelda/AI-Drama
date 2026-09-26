import { forward } from "@/lib/backend";

/**
 * 「测试连接」查 ComfyUI 是否在线。
 * 唯一数据源是后端：读它的 system/health 里的 comfyui 探测结果，避免前端再存一份设置。
 */
export async function GET() {
  const res = await forward("/api/system/health");
  const data: any = await res.json().catch(() => ({}));
  const comfy = data?.checks?.comfyui || {};
  return Response.json({ available: !!comfy.ok, url: comfy.url || "" });
}
