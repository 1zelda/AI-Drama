import { NextRequest } from "next/server";
import { forward } from "@/lib/backend";

/**
 * 审批动作。注意路径必须带 `/action` —— 页面调用的是
 * `/api/projects/{id}/approvals/{itemId}/action`，和后端
 * `POST /api/projects/{id}/approvals/{item_id}/action` 对齐。
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; item: string }> },
) {
  const { id, item } = await params;
  const body = await req.json().catch(() => ({}));
  return forward(`/api/projects/${id}/approvals/${item}/action`, {
    method: "POST",
    body: { action: body.action || "approve", feedback: body.feedback || "" },
  });
}
