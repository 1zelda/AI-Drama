"use client";
/**
 * 剧情工坊 — 范式页：素材+题材 → 自动生成剧情 → 可编辑 → 不满意覆盖重生成（需确认）→
 * 单集/连续剧 → 侧边对话框与 AI 策划讨论后再定稿。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, BookOpen, Check, Copy, Loader2, RefreshCw, Save, Wand2 } from "lucide-react";
import StoryChat from "@/components/StoryChat";
import TopNav from "@/components/TopNav";

const API = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

const GENRES = [
  "搞笑鬼畜", "逆袭爽剧", "悬疑反转", "甜宠恋爱", "科幻末日",
  "古装仙侠", "都市现实", "恐怖惊悚", "同人二创", "温情治愈",
];

export default function StudioPage() {
  const [sessionId, setSessionId] = useState<string>("");
  const [material, setMaterial] = useState("");
  const [genre, setGenre] = useState("搞笑鬼畜");
  const [mode, setMode] = useState<"single" | "series">("single");
  const [episodeCount, setEpisodeCount] = useState(3);
  const [instruction, setInstruction] = useState("");
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false); // 有未保存的手改
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedTip, setSavedTip] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const hasContent = content.trim().length > 0;

  const doGenerate = useCallback(async (isRegen: boolean) => {
    setConfirmOpen(false);
    setGenerating(true);
    setError("");
    try {
      const res = await fetch(`${API}/api/story/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId || undefined,
          material, genre, mode, episode_count: episodeCount,
          instruction,
          current_content: isRegen ? content : "",
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "生成失败");
      setSessionId(data.session_id);
      setContent(data.content);
      setDirty(false);
      setInstruction("");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  }, [sessionId, material, genre, mode, episodeCount, instruction, content]);

  const onRegenClick = () => {
    if (!hasContent) { doGenerate(false); return; }
    setConfirmOpen(true); // 覆盖前必须确认
  };

  const saveContent = async () => {
    if (!sessionId) return;
    setSaving(true);
    try {
      await fetch(`${API}/api/story/session/${sessionId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      setDirty(false);
      setSavedTip(true);
      setTimeout(() => setSavedTip(false), 1500);
    } finally { setSaving(false); }
  };

  const copyContent = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const input = {
    width: "100%", padding: "0.7rem 0.9rem", background: "#141414",
    border: "1px solid #2a2a2a", borderRadius: 8, color: "#fff",
    fontSize: "0.87rem", outline: "none", boxSizing: "border-box" as const, fontFamily: "inherit",
  };
  const label = { display: "block", marginBottom: "0.4rem", color: "#9ca3af", fontSize: "0.8rem", fontWeight: 600 };

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav
        title="剧情工坊"
        subtitle="素材 + 题材 → 自动剧情 → 讨论打磨 → 定稿"
        actions={sessionId ? <span style={{ fontSize: "0.72rem", color: "#666" }}>会话 {sessionId}</span> : null}
      />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.25rem", maxWidth: 1500, margin: "0 auto", padding: "1.5rem 1.25rem", alignItems: "start" }}>
        {/* 左列：输入 + 剧情稿 */}
        <div>
          <section style={{ marginBottom: "1rem", background: "#111", borderRadius: 12, padding: "1.1rem", border: "1px solid #1e1e1e" }}>
            <label style={label}>① 素材（小说 / 梗概 / 热点 / 一句话点子，均可）</label>
            <textarea value={material} onChange={(e) => setMaterial(e.target.value)} rows={5}
              placeholder={'例：元首愤怒鬼畜素材 + 原神抽卡梗；或直接贴一段小说原文…'}
              style={{ ...input, resize: "vertical" }} />

            <label style={{ ...label, marginTop: "0.9rem" }}>② 题材</label>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {GENRES.map((g) => (
                <button key={g} onClick={() => setGenre(g)}
                  style={{ padding: "0.35rem 0.8rem", background: genre === g ? "#6366f1" : "#1a1a1a", border: `1px solid ${genre === g ? "#6366f1" : "#2a2a2a"}`, borderRadius: 999, color: "#fff", fontSize: "0.78rem", cursor: "pointer" }}>
                  {g}
                </button>
              ))}
            </div>

            <label style={{ ...label, marginTop: "0.9rem" }}>③ 体量</label>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <button onClick={() => setMode("single")}
                style={{ padding: "0.45rem 1rem", background: mode === "single" ? "#10b981" : "#1a1a1a", border: `1px solid ${mode === "single" ? "#10b981" : "#2a2a2a"}`, borderRadius: 8, color: mode === "single" ? "#012" : "#fff", fontWeight: 600, fontSize: "0.82rem", cursor: "pointer" }}>
                单集
              </button>
              <button onClick={() => setMode("series")}
                style={{ padding: "0.45rem 1rem", background: mode === "series" ? "#6366f1" : "#1a1a1a", border: `1px solid ${mode === "series" ? "#6366f1" : "#2a2a2a"}`, borderRadius: 8, color: "#fff", fontWeight: 600, fontSize: "0.82rem", cursor: "pointer" }}>
                连续剧
              </button>
              {mode === "series" && (
                <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1 }}>
                  <input type="range" min={2} max={12} value={episodeCount} onChange={(e) => setEpisodeCount(+e.target.value)} style={{ flex: 1, accentColor: "#6366f1" }} />
                  <span style={{ fontSize: "0.85rem", color: "#a5b4fc", fontWeight: 700, minWidth: 42 }}>{episodeCount} 集</span>
                </div>
              )}
            </div>

            <label style={{ ...label, marginTop: "0.9rem" }}>④ 补充要求（可选，重新生成时作为修改意见）</label>
            <input value={instruction} onChange={(e) => setInstruction(e.target.value)}
              placeholder="例：结尾再狠一点 / 不要谈恋爱 / 多玩空耳梗"
              style={input} />

            <button onClick={onRegenClick} disabled={generating}
              style={{ width: "100%", marginTop: "1rem", padding: "0.9rem", background: generating ? "#333" : "linear-gradient(135deg,#6366f1,#8b5cf6)", color: "#fff", border: "none", borderRadius: 10, fontSize: "1rem", fontWeight: 700, cursor: generating ? "wait" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: "0.5rem" }}>
              {generating ? <><Loader2 size={18} style={{ animation: "spin 1s linear infinite" }} /> 生成中（约 30-60 秒）…</>
                : hasContent ? <><RefreshCw size={17} /> 重新生成（覆盖当前剧情稿）</>
                : <><Wand2 size={17} /> 自动生成剧情</>}
            </button>
            {error && <div style={{ marginTop: "0.6rem", padding: "0.5rem 0.8rem", background: "#2a1214", border: "1px solid #ef4444", borderRadius: 8, color: "#fca5a5", fontSize: "0.78rem" }}>{error}</div>}
          </section>

          <section style={{ background: "#111", borderRadius: 12, padding: "1.1rem", border: "1px solid #1e1e1e" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.7rem" }}>
              <label style={{ ...label, margin: 0 }}>剧情稿（可直接编辑，改完记得保存）</label>
              <div style={{ display: "flex", gap: 6 }}>
                <button onClick={copyContent} disabled={!hasContent}
                  style={{ display: "flex", alignItems: "center", gap: 4, padding: "0.35rem 0.7rem", background: "#1a1a1a", border: "1px solid #2a2a2a", borderRadius: 6, color: copied ? "#22c55e" : "#aaa", fontSize: "0.75rem", cursor: hasContent ? "pointer" : "default" }}>
                  {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "已复制" : "复制"}
                </button>
                <button onClick={saveContent} disabled={!sessionId || !hasContent || saving}
                  style={{ display: "flex", alignItems: "center", gap: 4, padding: "0.35rem 0.7rem", background: dirty ? "#10b981" : "#1a1a1a", border: "1px solid #2a2a2a", borderRadius: 6, color: dirty ? "#012" : "#aaa", fontWeight: dirty ? 700 : 400, fontSize: "0.75rem", cursor: hasContent ? "pointer" : "default" }}>
                  {saving ? <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} /> : savedTip ? <Check size={12} /> : <Save size={12} />} {savedTip ? "已保存" : "保存"}
                </button>
              </div>
            </div>
            <textarea value={content} onChange={(e) => { setContent(e.target.value); setDirty(true); }}
              disabled={generating}
              placeholder={"生成后这里会出现完整剧情稿（梗概 / 人物 / 分集大纲），可以直接编辑修改…"}
              style={{ ...input, minHeight: 420, resize: "vertical", lineHeight: 1.7, fontSize: "0.84rem", opacity: generating ? 0.5 : 1 }}
            />
            {dirty && <p style={{ margin: "0.4rem 0 0", fontSize: "0.72rem", color: "#f59e0b" }}>⚠ 有未保存的修改</p>}
          </section>
        </div>

        {/* 右列：讨论对话框 */}
        <div style={{ position: "sticky", top: "1.5rem", height: "calc(100vh - 8rem)" }}>
          <StoryChat sessionId={sessionId} height="100%" onApplyToScript={(text) => { setContent((c) => (c ? c + "\n\n---\n\n" + text : text)); setDirty(true); }} />
        </div>
      </div>

      {/* 覆盖确认弹窗 */}
      {confirmOpen && (
        <div onClick={() => setConfirmOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ background: "#161616", border: "1px solid #2a2a2a", borderRadius: 14, padding: "1.5rem", maxWidth: 440, width: "90%" }}>
            <h3 style={{ fontSize: "1rem", marginBottom: "0.6rem", display: "flex", alignItems: "center", gap: 8 }}>
              <RefreshCw size={16} color="#f59e0b" /> 重新生成将覆盖当前剧情稿
            </h3>
            <p style={{ fontSize: "0.83rem", color: "#9ca3af", lineHeight: 1.7, marginBottom: "1rem" }}>
              当前剧情稿将被<b style={{ color: "#f59e0b" }}>整体替换</b>，且无法撤销。
              {dirty && " 注意：你有未保存的手动修改，覆盖后会丢失。"}
              <br />想保留讨论过的方向？先把右边的结论「追加进剧情稿」并保存。
            </p>
            <label style={{ ...label }}>给 AI 的修改意见（可选）</label>
            <input value={instruction} onChange={(e) => setInstruction(e.target.value)}
              placeholder="例：上一版太平了，反转要更炸"
              style={input} />
            <div style={{ display: "flex", gap: 10, marginTop: "1.1rem" }}>
              <button onClick={() => setConfirmOpen(false)}
                style={{ flex: 1, padding: "0.7rem", background: "#1a1a1a", border: "1px solid #2a2a2a", borderRadius: 8, color: "#ccc", cursor: "pointer", fontSize: "0.88rem" }}>
                取消
              </button>
              <button onClick={() => doGenerate(true)}
                style={{ flex: 1, padding: "0.7rem", background: "#f59e0b", border: "none", borderRadius: 8, color: "#012", fontWeight: 700, cursor: "pointer", fontSize: "0.88rem" }}>
                确认覆盖，重新生成
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
