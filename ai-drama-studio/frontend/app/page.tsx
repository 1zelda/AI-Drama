"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, RefreshCw, Trash2, Loader2, ChevronRight } from "lucide-react";
import TopNav from "@/components/TopNav";

type Project = {
  id: string;
  title: string;
  description: string;
  status: string;
  characters?: any[];
  episodes?: any[];
  plan?: any;
  updated_at?: string;
  created_at?: string;
};

const STATUS_META: Record<string, { label: string; color: string }> = {
  planning: { label: "策划中", color: "#6366f1" },
  generating: { label: "生成中", color: "#f59e0b" },
  completed: { label: "已完成", color: "#22c55e" },
};

function timeAgo(iso?: string) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

const SHORTCUTS: { href: string; icon: string; title: string; desc: string; accent?: boolean }[] = [
  { href: "/make", icon: "⚡", title: "一键成片", desc: "一句创意 → 剧本·分镜·生图·配音·字幕·成片", accent: true },
  { href: "/history", icon: "📺", title: "成片库", desc: "历次跑完的成片，可预览/下载" },
  { href: "/studio", icon: "📖", title: "剧情工坊", desc: "选题 → 分集剧本 → 角色与场景" },
  { href: "/pipeline", icon: "🎬", title: "流水线画布", desc: "DAG 编排、实时进度、产物预览" },
  { href: "/assets", icon: "🗂️", title: "资产库", desc: "参考图/角色定妆，按标签复用" },
  { href: "/export", icon: "🎞️", title: "剪映导出", desc: "多轨草稿，交给人工精剪" },
];

export default function Home() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/projects", { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      setProjects(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message || "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const remove = async (p: Project) => {
    if (!window.confirm(`删除项目「${p.title}」？此操作不可撤销。`)) return;
    setDeleting(p.id);
    try {
      const res = await fetch(`/api/projects/${p.id}`, { method: "DELETE" });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d?.error || `HTTP ${res.status}`);
      }
      setProjects((list) => list.filter((x) => x.id !== p.id));
    } catch (e: any) {
      setError(`删除失败：${e.message}`);
    } finally {
      setDeleting(null);
    }
  };

  return (
    <main style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav
        actions={
          <>
            <button
              onClick={load}
              disabled={loading}
              title="刷新"
              style={iconBtn}
            >
              {loading ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}
            </button>
            <Link href="/new" style={primaryBtn}>
              <Plus size={15} /> 新建项目
            </Link>
          </>
        }
      />

      <div style={{ maxWidth: 1180, margin: "0 auto", padding: "1.75rem 1.25rem 4rem" }}>
        {/* Hero */}
        <section style={{ marginBottom: "1.75rem" }}>
          <h1
            style={{
              fontSize: "1.9rem",
              fontWeight: 800,
              margin: "0 0 .5rem",
              background: "linear-gradient(135deg,#6366f1,#a78bfa,#f472b6)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}
          >
            一站式 AI 短剧工作台
          </h1>
          <p style={{ margin: 0, color: "#8b8b8b", fontSize: "0.92rem", lineHeight: 1.7 }}>
            剧本 → 角色/场景资产 → 分镜 → 生图 → 生视频 → 配音 → 剪映草稿。
            每一步都能人工确认，产物随时可预览。
          </p>
        </section>

        {error && (
          <div style={banner}>
            <span style={{ flex: 1 }}>⚠️ {error}</span>
            <button onClick={load} style={linkBtn}>
              重试
            </button>
          </div>
        )}

        {/* 快捷入口 */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))",
            gap: "0.75rem",
            marginBottom: "2rem",
          }}
        >
          {SHORTCUTS.map((s) => (
            <Link
              key={s.href}
              href={s.href}
              style={s.accent
                ? { ...shortcutCard, borderColor: "#6366f159", background: "#141233" }
                : shortcutCard}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: "1.05rem" }}>{s.icon}</span>
                <strong style={{ fontSize: "0.9rem", color: s.accent ? "#c7d2fe" : "#fff" }}>
                  {s.title}
                </strong>
                <ChevronRight size={14} style={{ marginLeft: "auto", color: "#555" }} />
              </div>
              <div style={{ fontSize: "0.76rem", color: "#7a7a7a" }}>{s.desc}</div>
            </Link>
          ))}
        </div>

        {/* 项目列表 */}
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 10,
            marginBottom: "0.9rem",
          }}
        >
          <h2 style={{ fontSize: "1.1rem", fontWeight: 700, margin: 0 }}>我的项目</h2>
          <span style={{ fontSize: "0.78rem", color: "#666" }}>
            {loading ? "加载中…" : `${projects.length} 个`}
          </span>
        </div>

        {loading ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))",
              gap: "1rem",
            }}
          >
            {[0, 1, 2].map((i) => (
              <div key={i} style={{ ...card, minHeight: 132, opacity: 0.5 }} />
            ))}
          </div>
        ) : projects.length === 0 ? (
          <div style={{ ...card, textAlign: "center", padding: "3rem 1rem" }}>
            <div style={{ fontSize: "2.6rem", marginBottom: "0.75rem" }}>🎭</div>
            <p style={{ color: "#8b8b8b", margin: "0 0 1.25rem", fontSize: "0.9rem" }}>
              还没有项目，先建一个，或直接进剧情工坊写剧本
            </p>
            <div style={{ display: "flex", gap: "0.6rem", justifyContent: "center", flexWrap: "wrap" }}>
              <Link href="/new" style={primaryBtn}>
                <Plus size={15} /> 新建项目
              </Link>
              <Link href="/studio" style={ghostBtn}>
                进入剧情工坊
              </Link>
            </div>
          </div>
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))",
              gap: "1rem",
            }}
          >
            {projects.map((p) => {
              const meta = STATUS_META[p.status] || STATUS_META.planning;
              return (
                <div key={p.id} style={card}>
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                    <Link
                      href={`/project/${p.id}`}
                      style={{
                        flex: 1,
                        textDecoration: "none",
                        color: "#fff",
                        fontSize: "1rem",
                        fontWeight: 600,
                        lineHeight: 1.4,
                      }}
                    >
                      {p.title || "未命名项目"}
                    </Link>
                    <span
                      style={{
                        fontSize: "0.68rem",
                        padding: "0.15rem 0.5rem",
                        borderRadius: 999,
                        background: `${meta.color}22`,
                        color: meta.color,
                        whiteSpace: "nowrap",
                        flexShrink: 0,
                      }}
                    >
                      {meta.label}
                    </span>
                  </div>

                  {p.description && (
                    <p
                      style={{
                        margin: "0.45rem 0 0",
                        fontSize: "0.8rem",
                        color: "#7a7a7a",
                        lineHeight: 1.6,
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                      }}
                    >
                      {p.description}
                    </p>
                  )}

                  <div
                    style={{
                      display: "flex",
                      gap: "0.9rem",
                      fontSize: "0.75rem",
                      color: "#666",
                      marginTop: "0.7rem",
                    }}
                  >
                    <span>👤 {p.characters?.length || 0} 角色</span>
                    <span>📺 {p.episodes?.length || 0} 集</span>
                    {p.updated_at && <span style={{ marginLeft: "auto" }}>{timeAgo(p.updated_at)}</span>}
                  </div>

                  <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.9rem" }}>
                    <Link href={`/project/${p.id}`} style={{ ...ghostBtn, flex: 1, justifyContent: "center" }}>
                      打开策划
                    </Link>
                    <Link href="/pipeline" style={{ ...ghostBtn, justifyContent: "center" }}>
                      流水线
                    </Link>
                    <button
                      onClick={() => remove(p)}
                      disabled={deleting === p.id}
                      title="删除项目"
                      style={{ ...iconBtn, borderColor: "#3a2020", color: "#ef5350" }}
                    >
                      {deleting === p.id ? <Loader2 size={14} className="spin" /> : <Trash2 size={14} />}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <style jsx global>{`
        .spin {
          animation: spin 0.9s linear infinite;
        }
        @keyframes spin {
          to {
            transform: rotate(360deg);
          }
        }
      `}</style>
    </main>
  );
}

const card: React.CSSProperties = {
  padding: "1.1rem",
  background: "#121212",
  borderRadius: 14,
  border: "1px solid #232323",
};

const shortcutCard: React.CSSProperties = {
  ...card,
  padding: "0.85rem 1rem",
  textDecoration: "none",
  color: "#fff",
  transition: "border-color .15s, background .15s",
};

const primaryBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "0.45rem 0.9rem",
  background: "#6366f1",
  color: "#fff",
  borderRadius: 9,
  textDecoration: "none",
  fontSize: "0.82rem",
  fontWeight: 600,
  border: "none",
};

const ghostBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "0.4rem 0.8rem",
  background: "#171717",
  color: "#ddd",
  border: "1px solid #2a2a2a",
  borderRadius: 9,
  textDecoration: "none",
  fontSize: "0.78rem",
};

const iconBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 32,
  height: 32,
  background: "#171717",
  color: "#bbb",
  border: "1px solid #2a2a2a",
  borderRadius: 9,
  cursor: "pointer",
};

const banner: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "0.7rem 1rem",
  background: "#2a1214",
  border: "1px solid #7f1d1d",
  borderRadius: 10,
  color: "#fca5a5",
  fontSize: "0.82rem",
  marginBottom: "1.25rem",
};

const linkBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#fca5a5",
  textDecoration: "underline",
  cursor: "pointer",
  fontSize: "0.82rem",
};
