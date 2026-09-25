"use client";
/**
 * 剪映草稿导出：把生成的镜头片段组装成剪映多轨草稿（视频+转场+字幕+BGM）。
 * 后端: POST /api/export/jianying → drama-pipeline/scripts/export_jianying_draft.py
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { Film, Loader2, Plus, Trash2, CheckCircle } from "lucide-react";
import { API_BASE, api } from "@/lib/api";
import TopNav from "@/components/TopNav";

type Shot = { file: string; transition: string; subtitle: string };

const TRANSITIONS = ["", "叠化", "信号故障", "闪黑", "闪白", "推近", "拉远", "模糊"];

export default function ExportPage() {
  const [name, setName] = useState("我的新剧EP01");
  const [shots, setShots] = useState<Shot[]>([{ file: "", transition: "", subtitle: "" }]);
  const [bgm, setBgm] = useState("");
  const [srt, setSrt] = useState("");
  const [width, setWidth] = useState(1080);
  const [height, setHeight] = useState(1920);
  const [draftRoot, setDraftRoot] = useState("auto");
  const [scriptOk, setScriptOk] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string>("");
  const [error, setError] = useState<string>("");

  useEffect(() => {
    api<{ script_available: boolean; script_path: string }>("/api/export/jianying/info")
      .then((d) => setScriptOk(d.script_available))
      .catch(() => setScriptOk(false));
  }, []);

  const updateShot = (i: number, key: keyof Shot, value: string) =>
    setShots((prev) => prev.map((s, j) => (j === i ? { ...s, [key]: value } : s)));

  const doExport = async (runExport: boolean) => {
    setBusy(true); setError(""); setResult("");
    try {
      const res = await fetch(`${API_BASE}/api/export/jianying`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name, shots: shots.filter((s) => s.file.trim()),
          bgm: bgm || null, srt: srt || null,
          width, height, draft_root: draftRoot, run_export: runExport,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "导出失败");
      setResult(data.output || data.draft_path || "完成");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const input = { width: "100%", padding: "0.6rem", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 8, color: "#fff", fontSize: "0.85rem", outline: "none", boxSizing: "border-box" as const };

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav
        title="剪映草稿导出"
        subtitle="多轨草稿（视频+转场+字幕+BGM），导出后在剪映里人工精剪"
        actions={
          scriptOk === null ? null : scriptOk ? (
            <span style={{ color: "#22c55e", fontSize: "0.75rem" }}>✔ 导出脚本就绪</span>
          ) : (
            <span style={{ color: "#ef4444", fontSize: "0.75rem" }}>⚠ 脚本未就位</span>
          )
        }
      />

      <div style={{ maxWidth: 760, margin: "0 auto", padding: "2rem 1.25rem 4rem" }}>
        <section style={{ marginBottom: "1.5rem", background: "#111", borderRadius: 12, padding: "1.25rem", border: "1px solid #1e1e1e" }}>
          <h2 style={{ fontSize: "0.95rem", color: "#6366f1", marginBottom: "1rem" }}>单集信息</h2>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginBottom: "1rem" }}>
            <div>
              <label style={{ display: "block", marginBottom: 4, color: "#aaa", fontSize: "0.8rem" }}>草稿名</label>
              <input style={input} value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <label style={{ display: "block", marginBottom: 4, color: "#aaa", fontSize: "0.8rem" }}>画幅（宽 × 高）</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input style={input} type="number" value={width} onChange={(e) => setWidth(+e.target.value)} />
                <input style={input} type="number" value={height} onChange={(e) => setHeight(+e.target.value)} />
              </div>
            </div>
            <div>
              <label style={{ display: "block", marginBottom: 4, color: "#aaa", fontSize: "0.8rem" }}>BGM 音频路径（可选）</label>
              <input style={input} value={bgm} onChange={(e) => setBgm(e.target.value)} placeholder="output/EP01/bgm.mp3" />
            </div>
            <div>
              <label style={{ display: "block", marginBottom: 4, color: "#aaa", fontSize: "0.8rem" }}>SRT 字幕文件（可选）</label>
              <input style={input} value={srt} onChange={(e) => setSrt(e.target.value)} placeholder="output/EP01/EP01.srt" />
            </div>
          </div>
          <div>
            <label style={{ display: "block", marginBottom: 4, color: "#aaa", fontSize: "0.8rem" }}>剪映草稿根目录（auto = 自动探测默认目录）</label>
            <input style={input} value={draftRoot} onChange={(e) => setDraftRoot(e.target.value)} />
          </div>
        </section>

        <section style={{ marginBottom: "1.5rem", background: "#111", borderRadius: 12, padding: "1.25rem", border: "1px solid #1e1e1e" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
            <h2 style={{ fontSize: "0.95rem", color: "#6366f1" }}>镜头列表（按顺序）</h2>
            <button onClick={() => setShots((p) => [...p, { file: "", transition: "", subtitle: "" }])}
              style={{ display: "flex", alignItems: "center", gap: 4, padding: "0.4rem 0.8rem", background: "#6366f1", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: "0.8rem" }}>
              <Plus size={14} /> 加镜头
            </button>
          </div>
          {shots.map((s, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "2fr 1fr 2fr auto", gap: 8, marginBottom: 8, alignItems: "center" }}>
              <input style={input} value={s.file} onChange={(e) => updateShot(i, "file", e.target.value)} placeholder={`output/EP01/s${i + 1}.mp4`} />
              <select style={input} value={s.transition} onChange={(e) => updateShot(i, "transition", e.target.value)}>
                {TRANSITIONS.map((t) => <option key={t} value={t}>{t || "硬切"}</option>)}
              </select>
              <input style={input} value={s.subtitle} onChange={(e) => updateShot(i, "subtitle", e.target.value)} placeholder="字幕（可空，用 SRT 替代）" />
              <button onClick={() => setShots((p) => p.filter((_, j) => j !== i))}
                style={{ background: "transparent", border: "none", color: "#ef4444", cursor: "pointer", padding: 6 }}>
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </section>

        <button onClick={() => doExport(false)} disabled={busy || scriptOk === false}
          style={{ width: "100%", padding: "1rem", background: busy ? "#333" : "linear-gradient(135deg,#6366f1,#8b5cf6)", color: "#fff", border: "none", borderRadius: 10, fontSize: "1.05rem", fontWeight: 600, cursor: busy ? "wait" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: "0.5rem", marginBottom: "0.75rem" }}>
          {busy ? <><Loader2 size={18} style={{ animation: "spin 1s linear infinite" }} /> 导出中...</> : <><CheckCircle size={18} /> 生成剪映草稿</>}
        </button>
        <button onClick={() => doExport(true)} disabled={busy || scriptOk === false}
          style={{ width: "100%", padding: "0.75rem", background: "#1a1a1a", color: "#aaa", border: "1px solid #2a2a2a", borderRadius: 10, fontSize: "0.9rem", cursor: busy ? "wait" : "pointer" }}>
          生成草稿并调起剪映自动导出 MP4（需本机剪映 ≤6）
        </button>

        {error && <pre style={{ marginTop: "1rem", padding: "1rem", background: "#2a1214", border: "1px solid #ef4444", borderRadius: 8, color: "#fca5a5", fontSize: "0.78rem", whiteSpace: "pre-wrap" }}>{error}</pre>}
        {result && <pre style={{ marginTop: "1rem", padding: "1rem", background: "#0f2417", border: "1px solid #22c55e", borderRadius: 8, color: "#86efac", fontSize: "0.78rem", whiteSpace: "pre-wrap" }}>{result}</pre>}
      </div>
    </div>
  );
}
