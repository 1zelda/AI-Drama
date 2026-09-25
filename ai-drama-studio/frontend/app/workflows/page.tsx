"use client";

/**
 * 工作流目录：列出后端 config/workflows/ 下可用的流水线。
 *
 * 旧版这里有个「执行」按钮，POST 到 /api/workflows/execute —— 那个接口根本不存在，
 * 点了必然 404。执行统一走流水线页（有 SSE 进度、产物预览），这里只负责列目录和引导。
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import TopNav from "@/components/TopNav";

type Wf = { name: string };

export default function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<Wf[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    api<{ workflows: string[] }>("/api/runs/")
      .then((d) => {
        if (!alive) return;
        setWorkflows((d.workflows || []).map((n) => ({ name: n })));
      })
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <main style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav
        title="工作流目录"
        subtitle="执行请到流水线页，那里有实时进度和产物预览"
        actions={
          <Link
            href="/pipeline"
            style={{
              padding: "0.4rem 0.9rem",
              background: "#6366f1",
              color: "#fff",
              borderRadius: 9,
              textDecoration: "none",
              fontSize: "0.78rem",
              fontWeight: 600,
            }}
          >
            去流水线
          </Link>
        }
      />

      <div style={{ maxWidth: 720, margin: "0 auto", padding: "1.75rem 1.25rem 4rem" }}>
        {error && (
          <div
            style={{
              padding: "0.7rem 1rem",
              background: "#2a1214",
              border: "1px solid #7f1d1d",
              borderRadius: 10,
              color: "#fca5a5",
              fontSize: "0.82rem",
              marginBottom: "1.25rem",
            }}
          >
            ⚠️ {error}
          </div>
        )}

        {loading ? (
          <p style={{ color: "#666", fontSize: "0.85rem" }}>加载中…</p>
        ) : workflows.length === 0 && !error ? (
          <p style={{ color: "#666", fontSize: "0.85rem" }}>
            没有找到工作流（backend/config/workflows/*.json）
          </p>
        ) : (
          <div style={{ display: "grid", gap: "0.75rem" }}>
            {workflows.map((w) => (
              <Link
                key={w.name}
                href="/pipeline"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "0.9rem 1.1rem",
                  background: "#111",
                  border: "1px solid #232323",
                  borderRadius: 12,
                  textDecoration: "none",
                  color: "#fff",
                }}
              >
                <span style={{ fontSize: "1.1rem" }}>🧩</span>
                <code style={{ flex: 1, fontSize: "0.88rem", color: "#c7d2fe" }}>{w.name}</code>
                <span style={{ fontSize: "0.75rem", color: "#666" }}>在流水线中打开 →</span>
              </Link>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
