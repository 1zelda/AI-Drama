"use client";
/**
 * 配音与音乐台 — 声音克隆 TTS（GPT-SoVITS 参考音频零样本克隆）+ AI 音乐（LLM 写词 → ACE-Step 出曲）。
 * 服务离线时给出部署指引（见 drama-pipeline/音频克隆与AI音乐部署.md）。
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2, Mic2, Music4, Plus, UserRoundPlus } from "lucide-react";
import TopNav from "@/components/TopNav";

const API = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

type Voice = { name: string; engine: string; ref_audio: string; prompt_text: string; note?: string };

export default function AudioPage() {
  const [tab, setTab] = useState<"dub" | "song">("dub");
  const [voices, setVoices] = useState<Voice[]>([]);
  const [services, setServices] = useState<any>(null);

  // 配音
  const [text, setText] = useState("这破卡不抽也罢！\n我气到穿越了！");
  const [engine, setEngine] = useState<"edge" | "gptsovits">("edge");
  const [voiceName, setVoiceName] = useState("");
  const [busy, setBusy] = useState(false);
  const [clips, setClips] = useState<{ url: string; text: string }[]>([]);
  const [dubError, setDubError] = useState("");

  // 音色管理
  const [addOpen, setAddOpen] = useState(false);
  const [nvName, setNvName] = useState("");
  const [nvPrompt, setNvPrompt] = useState("");
  const [nvFile, setNvFile] = useState<File | null>(null);
  const [nvBusy, setNvBusy] = useState(false);

  // 歌曲
  const [theme, setTheme] = useState("元首的愤怒：在提瓦特大陆抽卡总是歪七七的悲愤");
  const [styleTags, setStyleTags] = useState("epic rock, dramatic, chinese, male vocal");
  const [duration, setDuration] = useState(60);
  const [lyrics, setLyrics] = useState("");
  const [lyricsBusy, setLyricsBusy] = useState(false);
  const [songBusy, setSongBusy] = useState(false);
  const [songRefs, setSongRefs] = useState<string[]>([]);
  const [songError, setSongError] = useState("");

  const loadVoices = () => fetch(`${API}/api/audio/voices`).then((r) => r.json()).then((d) => setVoices(d.voices || [])).catch(() => {});
  useEffect(() => {
    loadVoices();
    fetch(`${API}/api/audio/services`).then((r) => r.json()).then(setServices).catch(() => {});
  }, []);

  const fileToB64 = (f: File) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(f);
  });

  const addVoice = async () => {
    if (!nvName || !nvFile || !nvPrompt.trim()) return;
    setNvBusy(true);
    try {
      const b64 = await fileToB64(nvFile);
      const ext = nvFile.name.split(".").pop() || "wav";
      const res = await fetch(`${API}/api/audio/voices`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nvName, ref_audio_b64: b64, ref_audio_ext: ext, prompt_text: nvPrompt.trim() }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "添加失败");
      setAddOpen(false); setNvName(""); setNvPrompt(""); setNvFile(null);
      loadVoices();
    } catch (e: any) { alert(e.message); } finally { setNvBusy(false); }
  };

  const doTTS = async () => {
    setBusy(true); setDubError("");
    try {
      const lines = text.split("\n").filter((l) => l.trim());
      const results: { url: string; text: string }[] = [];
      for (const line of lines) {
        const res = await fetch(`${API}/api/audio/tts`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: line, engine, voice_name: voiceName || undefined }),
        });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || "TTS 失败");
        results.push({ url: `${API}${d.url}`, text: line });
      }
      setClips(results);
    } catch (e: any) { setDubError(e.message); } finally { setBusy(false); }
  };

  const doLyrics = async () => {
    setLyricsBusy(true); setSongError("");
    try {
      const res = await fetch(`${API}/api/audio/lyrics`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme, style_tags: styleTags, duration }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "写词失败");
      setLyrics(d.lyrics);
    } catch (e: any) { setSongError(e.message); } finally { setLyricsBusy(false); }
  };

  const doSong = async () => {
    setSongBusy(true); setSongError(""); setSongRefs([]);
    try {
      const res = await fetch(`${API}/api/audio/song`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme, style_tags: styleTags, lyrics, duration }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "生成歌曲失败");
      setSongRefs(d.audio_refs || []);
    } catch (e: any) { setSongError(e.message); } finally { setSongBusy(false); }
  };

  const input = { width: "100%", padding: "0.7rem 0.9rem", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 8, color: "#fff", fontSize: "0.87rem", outline: "none", boxSizing: "border-box" as const, fontFamily: "inherit" };
  const label = { display: "block", marginBottom: "0.4rem", color: "#9ca3af", fontSize: "0.8rem", fontWeight: 600 };

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav
        title="配音与音乐台"
        subtitle="多角色 TTS + AI 作曲"
        actions={
          services ? (
            <span style={{ fontSize: "0.72rem", color: "#777" }}>
              GPT-SoVITS：{services.gptsovits_online ? <b style={{ color: "#22c55e" }}>在线</b> : <span>离线</span>}
              　ACE-Step：{services.acestep_online ? <b style={{ color: "#22c55e" }}>在线</b> : <span>离线</span>}
            </span>
          ) : null
        }
      />

      <div style={{ maxWidth: 900, margin: "0 auto", padding: "1.5rem 2rem" }}>
        <div style={{ display: "flex", gap: 8, marginBottom: "1.2rem" }}>
          <button onClick={() => setTab("dub")} style={{ padding: "0.55rem 1.2rem", background: tab === "dub" ? "#6366f1" : "#1a1a1a", border: "1px solid #2a2a2a", borderRadius: 8, color: "#fff", fontWeight: 600, cursor: "pointer" }}>声音克隆配音</button>
          <button onClick={() => setTab("song")} style={{ padding: "0.55rem 1.2rem", background: tab === "song" ? "#8b5cf6" : "#1a1a1a", border: "1px solid #2a2a2a", borderRadius: 8, color: "#fff", fontWeight: 600, cursor: "pointer" }}>AI 音乐（写词+出曲）</button>
        </div>

        {tab === "dub" && (
          <section style={{ background: "#111", borderRadius: 12, padding: "1.1rem", border: "1px solid #1e1e1e", marginBottom: "1.2rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <label style={label}>音色库</label>
              <button onClick={() => setAddOpen(!addOpen)} style={{ display: "flex", alignItems: "center", gap: 4, padding: "0.35rem 0.8rem", background: "#1a1a1a", border: "1px solid #2a2a2a", borderRadius: 6, color: "#ccc", fontSize: "0.75rem", cursor: "pointer" }}>
                <UserRoundPlus size={12} /> 添加克隆音色
              </button>
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: "0.6rem" }}>
              {voices.length === 0 && <span style={{ color: "#555", fontSize: "0.78rem" }}>还没有克隆音色。上传一段 5-15 秒的元首清晰人声 + 对应文字稿即可创建。</span>}
              {voices.map((v) => (
                <button key={v.name} onClick={() => { setEngine("gptsovits"); setVoiceName(v.name); }}
                  style={{ padding: "0.35rem 0.8rem", background: engine === "gptsovits" && voiceName === v.name ? "#10b981" : "#1a1a1a", border: "1px solid #2a2a2a", borderRadius: 999, color: engine === "gptsovits" && voiceName === v.name ? "#012" : "#ccc", fontSize: "0.78rem", fontWeight: 600, cursor: "pointer" }}>
                  🎙 {v.name}
                </button>
              ))}
            </div>
            {addOpen && (
              <div style={{ background: "#161616", border: "1px solid #2a2a2a", borderRadius: 10, padding: "1rem", marginBottom: "0.8rem" }}>
                <label style={label}>音色名</label>
                <input style={input} value={nvName} onChange={(e) => setNvName(e.target.value)} placeholder="例：元首" />
                <label style={{ ...label, marginTop: "0.7rem" }}>参考音频（5-15 秒清晰人声，wav/mp3）</label>
                <input type="file" accept=".wav,.mp3,.flac" onChange={(e) => setNvFile(e.target.files?.[0] || null)} style={{ ...input, padding: "0.5rem" }} />
                <label style={{ ...label, marginTop: "0.7rem" }}>参考音频文字稿（逐字对应！克隆质量的关键）</label>
                <input style={input} value={nvPrompt} onChange={(e) => setNvPrompt(e.target.value)} placeholder="这段音频里说的原话（德语原声就用德语原文，中文配音就用中文）" />
                <button onClick={addVoice} disabled={nvBusy || !nvName || !nvFile || !nvPrompt.trim()}
                  style={{ marginTop: "0.8rem", padding: "0.55rem 1rem", background: nvBusy ? "#333" : "#10b981", border: "none", borderRadius: 8, color: "#012", fontWeight: 700, cursor: "pointer", fontSize: "0.82rem", display: "flex", alignItems: "center", gap: 6 }}>
                  {nvBusy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Plus size={14} />} 创建音色
                </button>
              </div>
            )}

            <label style={{ ...label, marginTop: "0.5rem" }}>台词（每行一条，逐条生成）</label>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} style={{ ...input, resize: "vertical" }} />
            <div style={{ display: "flex", gap: 8, marginTop: "0.7rem", alignItems: "center" }}>
              <select value={engine} onChange={(e) => setEngine(e.target.value as any)} style={input}>
                <option value="edge">edge-tts（免费合成音，无克隆）</option>
                <option value="gptsovits" disabled={!voiceName}>GPT-SoVITS 克隆音色（需服务在线）</option>
              </select>
              <button onClick={doTTS} disabled={busy}
                style={{ padding: "0.7rem 1.3rem", background: busy ? "#333" : "#6366f1", border: "none", borderRadius: 8, color: "#fff", fontWeight: 700, cursor: busy ? "wait" : "pointer", whiteSpace: "nowrap" }}>
                {busy ? <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} /> : <Mic2 size={15} />} 生成配音
              </button>
            </div>
            {dubError && <div style={{ marginTop: "0.6rem", padding: "0.5rem 0.8rem", background: "#2a1214", border: "1px solid #ef4444", borderRadius: 8, color: "#fca5a5", fontSize: "0.78rem" }}>{dubError}</div>}
            {clips.length > 0 && (
              <div style={{ marginTop: "0.9rem" }}>
                {clips.map((c, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "0.5rem 0", borderTop: "1px solid #1e1e2a" }}>
                    <audio controls src={c.url} style={{ height: 34 }} />
                    <span style={{ fontSize: "0.8rem", color: "#aaa" }}>{c.text}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {tab === "song" && (
          <section style={{ background: "#111", borderRadius: 12, padding: "1.1rem", border: "1px solid #1e1e1e" }}>
            <label style={label}>歌曲主题 / 想表达的内容</label>
            <input value={theme} onChange={(e) => setTheme(e.target.value)} style={input} />
            <div style={{ display: "flex", gap: 10, marginTop: "0.7rem" }}>
              <div style={{ flex: 2 }}>
                <label style={label}>风格标签（给 ACE-Step）</label>
                <input value={styleTags} onChange={(e) => setStyleTags(e.target.value)} style={input} />
              </div>
              <div style={{ flex: 1 }}>
                <label style={label}>时长（秒）</label>
                <input type="number" value={duration} onChange={(e) => setDuration(+e.target.value)} min={15} max={240} style={input} />
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: "0.8rem" }}>
              <button onClick={doLyrics} disabled={lyricsBusy}
                style={{ flex: 1, padding: "0.75rem", background: lyricsBusy ? "#333" : "#6366f1", border: "none", borderRadius: 8, color: "#fff", fontWeight: 700, cursor: lyricsBusy ? "wait" : "pointer" }}>
                {lyricsBusy ? <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} /> : "✍ AI 写歌词"}
              </button>
              <button onClick={doSong} disabled={songBusy || !lyrics.trim()}
                style={{ flex: 1, padding: "0.75rem", background: songBusy || !lyrics.trim() ? "#333" : "#8b5cf6", border: "none", borderRadius: 8, color: "#fff", fontWeight: 700, cursor: songBusy ? "wait" : "pointer" }}>
                {songBusy ? <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} /> : <Music4 size={15} />} 生成歌曲（ACE-Step，需在线）
              </button>
            </div>
            {songError && <div style={{ marginTop: "0.7rem", padding: "0.5rem 0.8rem", background: "#2a1214", border: "1px solid #ef4444", borderRadius: 8, color: "#fca5a5", fontSize: "0.78rem" }}>{songError}</div>}
            <label style={{ ...label, marginTop: "0.9rem" }}>歌词（[verse]/[chorus] 结构，可手改后再生成）</label>
            <textarea value={lyrics} onChange={(e) => setLyrics(e.target.value)} rows={12}
              placeholder="点「AI 写歌词」自动生成，或手动粘贴（ACE-Step 格式：[verse] [chorus] 分段）"
              style={{ ...input, resize: "vertical", lineHeight: 1.7, fontSize: "0.82rem" }} />
            {songRefs.length > 0 && (
              <div style={{ marginTop: "0.9rem" }}>
                <p style={{ fontSize: "0.8rem", color: "#22c55e", margin: "0 0 0.4rem" }}>✔ 生成完成（文件在 ACE-Step 服务机上）：{songRefs.join(" , ")}</p>
                <p style={{ fontSize: "0.75rem", color: "#777", margin: 0 }}>
                  要换成元首音色唱：把输出人声在 RVC（已克隆模型）里做声音转换——步骤见 drama-pipeline/音频克隆与AI音乐部署.md
                </p>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
