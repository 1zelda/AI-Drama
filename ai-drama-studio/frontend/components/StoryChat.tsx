"use client";
/**
 * StoryChat — 剧情工坊讨论对话框（v2 重写版）。
 * 修复旧 ChatDialog 的痛点：无气泡布局、不自动滚屏、AI 无对话记忆、无快捷指令、报错不可见。
 * 对话记忆由后端 /api/story/chat 按 session_id 维护，这里同时本地镜像一份用于渲染。
 */
import { useEffect, useRef, useState } from "react";
import { Loader2, SendHorizonal, Sparkles } from "lucide-react";

type Msg = { role: "user" | "assistant" | "system"; content: string };

const QUICK_PROMPTS = [
  "更搞笑一点，加点空耳梗",
  "给主角加一个反转身份",
  "压成 3 集，节奏更快",
  "人物性格再极端一点",
  "第一集前 10 秒怎么抓人？",
  "帮我把讨论结论写进剧情稿",
];

export default function StoryChat({
  sessionId,
  onApplyToScript,
  height = "100%",
}: {
  sessionId: string;
  onApplyToScript?: (text: string) => void;
  height?: string | number;
}) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 载入历史（后端持久化）
  useEffect(() => {
    if (!sessionId) return;
    fetch(`${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}/api/story/session/${sessionId}`)
      .then((r) => r.json())
      .then((d) => setMessages((d.history || []).filter((m: Msg) => m.role !== "system")))
      .catch(() => {});
  }, [sessionId]);

  // 新消息自动滚到底
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const send = async (text?: string) => {
    const msg = (text ?? input).trim();
    if (!msg || loading) return;
    setInput("");
    setError("");
    setMessages((p) => [...p, { role: "user", content: msg }]);
    setLoading(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}/api/story/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: msg }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "对话失败");
      setMessages((p) => [...p, { role: "assistant", content: data.reply }]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const bubble = (m: Msg, i: number) => {
    const isUser = m.role === "user";
    return (
      <div key={i} style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", marginBottom: 12 }}>
        {!isUser && (
          <div style={{ width: 28, height: 28, borderRadius: 8, background: "linear-gradient(135deg,#10b981,#059669)", display: "flex", alignItems: "center", justifyContent: "center", marginRight: 8, flexShrink: 0, fontSize: 14 }}>
            🎬
          </div>
        )}
        <div style={{ maxWidth: "82%" }}>
          <div style={{
            padding: "0.65rem 0.9rem",
            borderRadius: isUser ? "12px 12px 3px 12px" : "12px 12px 12px 3px",
            background: isUser ? "#6366f1" : "#1c1c28",
            border: isUser ? "none" : "1px solid #2a2a3a",
            color: "#e8e8f0",
            fontSize: "0.86rem",
            lineHeight: 1.65,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}>
            {m.content}
          </div>
          {!isUser && onApplyToScript && (
            <button onClick={() => onApplyToScript(m.content)}
              style={{ marginTop: 4, background: "transparent", border: "none", color: "#6b7280", fontSize: "0.72rem", cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}>
              <Sparkles size={11} /> 把这段追加进剧情稿
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height, background: "#111", borderRadius: 12, border: "1px solid #1e1e2a", overflow: "hidden" }}>
      <div style={{ padding: "0.7rem 1rem", borderBottom: "1px solid #1e1e2a", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: "0.9rem", fontWeight: 600, color: "#10b981" }}>策划讨论</span>
        <span style={{ fontSize: "0.72rem", color: "#555" }}>AI 记得全部上下文（素材 · 剧情稿 · 你的每句话）</span>
      </div>

      <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: "1rem" }}>
        {messages.length === 0 && (
          <div style={{ color: "#555", textAlign: "center", padding: "2rem 1rem", fontSize: "0.85rem", lineHeight: 1.8 }}>
            先生成一版剧情，然后在这里跟 AI 策划讨论：<br />不满意哪就骂哪，聊完再点「重新生成」
          </div>
        )}
        {messages.map(bubble)}
        {loading && (
          <div style={{ display: "flex", gap: 4, padding: "0.5rem 1rem" }}>
            {[0, 1, 2].map((i) => (
              <span key={i} style={{ width: 6, height: 6, borderRadius: 3, background: "#10b981", opacity: 0.3 + i * 0.3, animation: `bounce 1s ${i * 0.15}s infinite` }} />
            ))}
          </div>
        )}
      </div>

      {error && (
        <div style={{ margin: "0 1rem 0.5rem", padding: "0.5rem 0.8rem", background: "#2a1214", border: "1px solid #ef4444", borderRadius: 8, color: "#fca5a5", fontSize: "0.75rem" }}>
          {error}
        </div>
      )}

      <div style={{ padding: "0 1rem 0.5rem", display: "flex", gap: 6, flexWrap: "wrap" }}>
        {QUICK_PROMPTS.map((q) => (
          <button key={q} onClick={() => send(q)} disabled={loading}
            style={{ padding: "0.3rem 0.7rem", background: "#1a1a26", border: "1px solid #2a2a3a", borderRadius: 999, color: "#9ca3af", fontSize: "0.72rem", cursor: loading ? "wait" : "pointer" }}>
            {q}
          </button>
        ))}
      </div>

      <div style={{ padding: "0.75rem", borderTop: "1px solid #1e1e2a", display: "flex", gap: 8, alignItems: "flex-end" }}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
          placeholder="和 AI 策划讨论剧情…（Enter 发送，Shift+Enter 换行）"
          rows={2}
          style={{ flex: 1, padding: "0.6rem 0.8rem", background: "#1a1a1a", border: "1px solid #2a2a3a", borderRadius: 10, color: "#fff", fontSize: "0.85rem", resize: "none", outline: "none", boxSizing: "border-box", fontFamily: "inherit" }}
        />
        <button onClick={() => send()} disabled={loading || !input.trim()}
          style={{ padding: "0.6rem 0.9rem", background: loading || !input.trim() ? "#333" : "#10b981", color: "#012", border: "none", borderRadius: 10, cursor: loading ? "wait" : "pointer", display: "flex", alignItems: "center" }}>
          {loading ? <Loader2 size={16} style={{ animation: "spin 1s linear infinite" }} /> : <SendHorizonal size={16} />}
        </button>
      </div>
    </div>
  );
}
