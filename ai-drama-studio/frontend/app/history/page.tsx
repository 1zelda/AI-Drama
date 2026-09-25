"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import TopNav from "@/components/TopNav";
import { API_BASE, api } from "@/lib/api";

/**
 * 成片库。
 *
 * 一键成片跑完的成片以前只躺在 output/ 里、跟中间产物混在一起，刷新一下就找不到。
 * 现在每次跑完由后端登记进 data/history.json（含封面），这里按时间倒序展示，
 * 支持在线播放 / 下载 / 删除，并能顺手清掉中间产物。
 */
type Record = {
  id: string;
  title: string;
  idea: string;
  workflow: string;
  created_at: number;
  duration?: number;
  width?: number;
  height?: number;
  size: number;
  clips?: number;
  video_url?: string;
  poster_url?: string;
  exists?: boolean;
};

export default function HistoryPage() {
  const [items, setItems] = useState<Record[]>([]);
  const [loading, setLoading] = useState(true);
  const [playing, setPlaying] = useState<Record | null>(null);
  const [plan, setPlan] = useState<any>(null);
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api<{ items: Record[] }>("/api/history/?limit=100");
      setItems(r.items || []);
    } catch (e: any) {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = async (rec: Record, keepFiles: boolean) => {
    setBusy(rec.id);
    try {
      await api(`/api/history/${rec.id}?keep_files=${keepFiles}`, { method: "DELETE" });
      if (playing?.id === rec.id) setPlaying(null);
      await load();
    } finally {
      setBusy("");
    }
  };

  const planCleanup = async () => {
    setBusy("cleanup");
    try {
      const r = await api("/api/system/cleanup", {
        method: "POST",
        body: JSON.stringify({ dry_run: true }),
      });
      setPlan(r);
    } finally {
      setBusy("");
    }
  };

  const doCleanup = async () => {
    setBusy("cleanup");
    try {
      const r = await api("/api/system/cleanup", {
        method: "POST",
        body: JSON.stringify({ dry_run: false }),
      });
      setPlan(null);
      alert(`已清理 ${r.deleted} 个文件，释放约 ${r.mb} MB`);
      await load();
    } finally {
      setBusy("");
    }
  };

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav title="成片库" subtitle="每次跑完的成片都会登记在这里" />
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "1.5rem 1.25rem 4rem" }}>

        <div style={{ display: "flex", gap: 10, marginBottom: 18, flexWrap: "wrap", alignItems: "center" }}>
          <button onClick={() => void load()} style={ghost}>刷新</button>
          <button onClick={() => void planCleanup()} disabled={busy === "cleanup"} style={ghost}>
            {busy === "cleanup" ? "扫描中…" : "清理中间产物"}
          </button>
          <span style={{ color: "#555", fontSize: "0.75rem" }}>
            共 {items.length} 条成片 · 中间产物指分镜图/单镜头视频/配音，成片和封面不会被删
          </span>
        </div>

        {plan && (
          <div style={{ ...box, border: "1px solid #78350f", background: "#1c1206", marginBottom: 18 }}>
            <div style={{ fontSize: "0.85rem", color: "#fcd34d", marginBottom: 8 }}>
              将要删除 <b>{plan.files}</b> 个文件，约 <b>{plan.mb} MB</b>
              {plan.tmp_dirs ? `（另含 ${plan.tmp_dirs} 个临时目录）` : ""}
            </div>
            {plan.samples?.length > 0 && (
              <div style={{ fontSize: "0.72rem", color: "#a1a1aa", marginBottom: 10, lineHeight: 1.6 }}>
                {plan.samples.join(" · ")}{plan.files > plan.samples.length ? " …" : ""}
              </div>
            )}
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={() => void doCleanup()} style={{ ...ghost, borderColor: "#b45309", color: "#fbbf24" }}>
                确认删除
              </button>
              <button onClick={() => setPlan(null)} style={ghost}>取消</button>
            </div>
          </div>
        )}

        {loading && <p style={{ color: "#555" }}>加载中…</p>}

        {!loading && items.length === 0 && (
          <div style={{ ...box, textAlign: "center", padding: "3rem 1rem" }}>
            <div style={{ fontSize: "2rem", marginBottom: 10 }}>🎬</div>
            <p style={{ color: "#888", margin: "0 0 6px" }}>还没有成片</p>
            <p style={{ color: "#555", fontSize: "0.8rem", margin: 0 }}>
              去
              <Link href="/make" style={{ color: "#818cf8" }}> 一键成片 </Link>
              填一句创意，跑完就会自动出现在这里
            </p>
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))", gap: 14 }}>
          {items.map((it) => (
            <div key={it.id} style={card}>
              <div
                onClick={() => it.exists && setPlaying(it)}
                style={{
                  position: "relative",
                  // 按成片真实画幅显示：横屏成片也要保持 16:9，写死 9:16 会把画面裁掉大半
                  aspectRatio: it.width && it.height ? `${it.width}/${it.height}` : "9/16",
                  borderRadius: 8, overflow: "hidden",
                  background: "#0d0d0d", cursor: it.exists ? "pointer" : "default",
                  border: "1px solid #1e1e1e",
                }}
              >
                {it.poster_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={`${API_BASE}${it.poster_url}`} alt={it.title}
                    style={{ width: "100%", height: "100%", objectFit: "contain" }} />
                ) : (
                  <div style={{ display: "grid", placeItems: "center", height: "100%", color: "#333", fontSize: "1.6rem" }}>🎞️</div>
                )}
                {!it.exists && (
                  <div style={{
                    position: "absolute", inset: 0, display: "grid", placeItems: "center",
                    background: "#000000aa", color: "#fca5a5", fontSize: "0.75rem",
                  }}>文件已丢失</div>
                )}
                <span style={{
                  position: "absolute", right: 6, bottom: 6, padding: "1px 6px", borderRadius: 4,
                  background: "#000000aa", fontSize: "0.68rem", color: "#ddd",
                }}>
                  {it.duration ? `${it.duration.toFixed(1)}s` : ""}
                </span>
              </div>

              <div style={{ marginTop: 8, fontSize: "0.85rem", fontWeight: 600, lineHeight: 1.4 }}
                title={it.title}>
                {it.title || "未命名成片"}
              </div>
              <div style={{ marginTop: 3, fontSize: "0.7rem", color: "#666" }}>
                {new Date(it.created_at * 1000).toLocaleString("zh-CN")}
                {it.width ? ` · ${it.width}×${it.height}` : ""}
                {it.size ? ` · ${(it.size / 1024 / 1024).toFixed(1)}MB` : ""}
              </div>

              <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
                <a href={`${API_BASE}${it.video_url}`} target="_blank" rel="noreferrer"
                  style={{ ...mini, textDecoration: "none", textAlign: "center" }}>下载</a>
                <button onClick={() => void remove(it, true)} disabled={busy === it.id}
                  style={mini}>删记录</button>
                <button onClick={() => void remove(it, false)} disabled={busy === it.id}
                  style={{ ...mini, color: "#fca5a5" }}>连文件</button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {playing && (
        <div
          onClick={() => setPlaying(null)}
          style={{
            position: "fixed", inset: 0, background: "#000000dd", display: "grid",
            placeItems: "center", zIndex: 50, padding: "2rem",
          }}
        >
          <div onClick={(e) => e.stopPropagation()} style={{ maxWidth: 480, width: "100%" }}>
            <video
              src={`${API_BASE}${playing.video_url}`}
              controls autoPlay playsInline
              style={{ width: "100%", borderRadius: 10, background: "#000" }}
            />
            <div style={{ marginTop: 10, fontSize: "0.9rem" }}>{playing.title}</div>
            <button onClick={() => setPlaying(null)} style={{ ...ghost, marginTop: 10 }}>关闭</button>
          </div>
        </div>
      )}
    </div>
  );
}

const box: React.CSSProperties = {
  background: "#111", border: "1px solid #1e1e1e", borderRadius: 12, padding: "1rem",
};
const card: React.CSSProperties = {
  background: "#111", border: "1px solid #1e1e1e", borderRadius: 12, padding: "0.7rem",
};
const ghost: React.CSSProperties = {
  padding: "0.5rem 0.9rem", background: "#1a1a1a", border: "1px solid #2a2a2a",
  borderRadius: 8, color: "#ddd", cursor: "pointer", fontSize: "0.82rem",
};
const mini: React.CSSProperties = {
  flex: 1, padding: "0.35rem 0.4rem", background: "#161616", border: "1px solid #262626",
  borderRadius: 6, color: "#bbb", cursor: "pointer", fontSize: "0.72rem",
};
