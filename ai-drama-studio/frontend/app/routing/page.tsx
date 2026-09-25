"use client";
/**
 * 镜头路由设置 — 按镜头类型配置模型派发/备选/禁用/水印预设/运动提示词模板。
 * 数据: GET/PUT /api/routing → backend/config/shot_routing.json
 */
import { useEffect, useState } from "react";
import { Check, Loader2, Save, ShieldAlert } from "lucide-react";
import TopNav from "@/components/TopNav";

const API = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

type ShotType = {
  id: string;
  label: string;
  preferred: string;
  fallback: string;
  banned: string[];
  watermark: string;
  motion_template: string;
  micro_actions: string[];
  banned_terms: string[];
  notes: string;
};
type Routing = { version: number; global: Record<string, any>; shot_types: ShotType[] };

export default function RoutingPage() {
  const [routing, setRouting] = useState<Routing | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API}/api/routing/`).then((r) => r.json()).then(setRouting).catch((e) => setError(String(e)));
  }, []);

  const updateST = (idx: number, key: keyof ShotType, value: any) =>
    setRouting((r) => r && ({ ...r, shot_types: r.shot_types.map((s, i) => (i === idx ? { ...s, [key]: value } : s)) }));

  const save = async () => {
    if (!routing) return;
    setSaving(true); setError("");
    try {
      const res = await fetch(`${API}/api/routing/`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(routing),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "保存失败");
      setRouting(d); setSaved(true); setTimeout(() => setSaved(false), 1500);
    } catch (e: any) { setError(e.message); } finally { setSaving(false); }
  };

  const input = { width: "100%", padding: "0.45rem 0.6rem", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 6, color: "#fff", fontSize: "0.78rem", outline: "none", boxSizing: "border-box" as const, fontFamily: "inherit" };

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav
        title="镜头路由设置"
        subtitle="流水线节点填 shot_type 即自动派发；显式指定 provider 优先生效"
        actions={
          <button onClick={save} disabled={saving || !routing}
            style={{ display: "flex", alignItems: "center", gap: 6, padding: "0.45rem 1rem", background: saving ? "#333" : saved ? "#22c55e" : "#6366f1", border: "none", borderRadius: 8, color: "#fff", fontWeight: 700, cursor: "pointer", fontSize: "0.8rem" }}>
            {saving ? <Loader2 size={14} className="spin" /> : saved ? <Check size={14} /> : <Save size={14} />} {saved ? "已保存" : "保存"}
          </button>
        }
      />
      {error && <div style={{ margin: "1rem 2rem", padding: "0.7rem 1rem", background: "#2a1214", border: "1px solid #ef4444", borderRadius: 8, color: "#fca5a5", fontSize: "0.8rem" }}>{error}</div>}

      {routing && (
        <div style={{ maxWidth: 1200, margin: "0 auto", padding: "1.5rem 2rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: "1rem", padding: "0.8rem 1rem", background: "#111", borderRadius: 10, border: "1px solid #1e1e1e" }}>
            <label style={{ fontSize: "0.85rem", color: "#ccc", display: "flex", alignItems: "center", gap: 8 }}>
              <input type="checkbox" checked={!!routing.global.auto_remove_watermark}
                onChange={(e) => setRouting({ ...routing, global: { ...routing.global, auto_remove_watermark: e.target.checked } })}
                style={{ accentColor: "#6366f1" }} />
              生成后自动去水印（LaMa 本地修复）
            </label>
            <span style={{ fontSize: "0.72rem", color: "#555" }}>水印预设按镜头类型的 watermark 字段取（zhipu/agnes）</span>
          </div>

          {routing.shot_types.map((st, idx) => (
            <div key={st.id} style={{ background: "#111", borderRadius: 12, padding: "1.1rem", border: "1px solid #1e1e1e", marginBottom: "1rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: "0.7rem" }}>
                <strong style={{ fontSize: "0.95rem", color: "#a5b4fc" }}>{st.label}</strong>
                <code style={{ fontSize: "0.72rem", color: "#555" }}>shot_type: {st.id}</code>
                {st.banned.length > 0 && (
                  <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4, fontSize: "0.72rem", color: "#f59e0b" }}>
                    <ShieldAlert size={12} /> 禁用: {st.banned.join(", ")}
                  </span>
                )}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, marginBottom: "0.7rem" }}>
                <div>
                  <label style={{ ...label }}>首选模型</label>
                  <input style={input} value={st.preferred} onChange={(e) => updateST(idx, "preferred", e.target.value)} />
                </div>
                <div>
                  <label style={{ ...label }}>备选</label>
                  <input style={input} value={st.fallback} onChange={(e) => updateST(idx, "fallback", e.target.value)} />
                </div>
                <div>
                  <label style={{ ...label }}>水印预设</label>
                  <input style={input} value={st.watermark} onChange={(e) => updateST(idx, "watermark", e.target.value)} placeholder="无则留空" />
                </div>
              </div>
              <label style={{ ...label }}>运动提示词模板（{'{micro_action}'} 为占位，内建「一片段一动词」纪律）</label>
              <input style={input} value={st.motion_template} onChange={(e) => updateST(idx, "motion_template", e.target.value)} />
              <p style={{ margin: "0.5rem 0 0", fontSize: "0.72rem", color: "#777", lineHeight: 1.6 }}>💡 {st.notes}</p>
            </div>
          ))}
        </div>
      )}

      <style jsx global>{`
        .spin { animation: spin .9s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

const label = { display: "block", marginBottom: "0.3rem", color: "#9ca3af", fontSize: "0.72rem", fontWeight: 600 };
