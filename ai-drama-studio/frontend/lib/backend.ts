/**
 * Next 路由处理器 → FastAPI 的转发层。
 *
 * 为什么需要它：页面里大量用的是相对路径 `/api/projects/...`，这些请求落在 Next 上。
 * 以前 Next 自己用 lib/store.ts 读写 `frontend/data/projects/*.json`，和 FastAPI 的
 * `backend/data/projects/*.json` 是两套互不相通的数据，首页新建的项目在流水线里看不到。
 * 现在统一由后端做唯一数据源，这里只做透传。
 */
import { NextResponse } from "next/server";

/** 后端地址。SSR 侧优先用 BACKEND_URL，便于部署时与浏览器侧地址区分。 */
export const BACKEND = (
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://127.0.0.1:8000"
).replace(/\/+$/, "");

type ForwardInit = {
  method?: string;
  body?: unknown;
  /** LLM 类接口较慢，默认给足超时 */
  timeoutMs?: number;
};

/**
 * 转发一个请求到后端，并把响应原样返回给浏览器。
 * 后端不可达/超时时返回 502 + 中文提示，而不是让页面静默失败。
 */
export async function forward(path: string, init: ForwardInit = {}) {
  const { method = "GET", body, timeoutMs = 180_000 } = init;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BACKEND}${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = text;
    }
    if (!res.ok) {
      const detail = (data as any)?.detail ?? (typeof data === "string" ? data : res.statusText);
      return NextResponse.json(
        { error: typeof detail === "string" ? detail : JSON.stringify(detail) },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch (e: any) {
    const reason = e?.name === "AbortError" ? "请求超时" : "后端不可达";
    return NextResponse.json(
      {
        error: `${reason}：${BACKEND}${path}（请先启动后端：cd backend && uvicorn app.main:app --port 8000）`,
      },
      { status: 502 },
    );
  } finally {
    clearTimeout(timer);
  }
}
