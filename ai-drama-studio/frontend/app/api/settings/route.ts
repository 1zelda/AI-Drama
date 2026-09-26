import { NextRequest } from "next/server";
import { forward } from "@/lib/backend";

/** 设置唯一数据源是后端（同步写 .env 并热更新进程），这里只做透传。 */
export async function GET() {
  return forward("/api/settings/");
}

export async function POST(req: NextRequest) {
  return forward("/api/settings/", { method: "PUT", body: await req.json() });
}

export async function PUT(req: NextRequest) {
  return forward("/api/settings/", { method: "PUT", body: await req.json() });
}
