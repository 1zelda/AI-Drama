"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import TopNav from "@/components/TopNav";

const GENRES = [
  { value: "drama", label: "剧情", emoji: "🎭" },
  { value: "romance", label: "甜宠", emoji: "💕" },
  { value: "action", label: "动作", emoji: "💥" },
  { value: "comedy", label: "搞笑", emoji: "😂" },
  { value: "horror", label: "悬疑惊悚", emoji: "👻" },
  { value: "scifi", label: "科幻", emoji: "🚀" },
  { value: "fantasy", label: "古装仙侠", emoji: "🧙" },
  { value: "thriller", label: "逆袭爽剧", emoji: "🔪" },
];

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "0.75rem 0.85rem",
  background: "#121212",
  border: "1px solid #2a2a2a",
  borderRadius: 10,
  color: "#fff",
  fontSize: "0.92rem",
  outline: "none",
  boxSizing: "border-box",
};

export default function NewProject() {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [genre, setGenre] = useState("drama");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || loading) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim(), description: description.trim(), genre }),
      });
      const data = await res.json();
      if (!res.ok || !data?.id) throw new Error(data?.error || `创建失败（HTTP ${res.status}）`);
      router.push(`/project/${data.id}`);
    } catch (err: any) {
      setError(err.message || "创建失败");
      setLoading(false);
    }
  };

  return (
    <main style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav title="新建项目" subtitle="建好之后可以直接 AI 策划，也可以进剧情工坊手写" />

      <div style={{ maxWidth: 620, margin: "0 auto", padding: "2rem 1.25rem 4rem" }}>
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

        <form onSubmit={submit}>
          <div style={{ marginBottom: "1.25rem" }}>
            <label style={label}>标题 *</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：照相馆里的时空相机"
              style={inputStyle}
              autoFocus
              required
            />
          </div>

          <div style={{ marginBottom: "1.25rem" }}>
            <label style={label}>题材</label>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: "0.6rem" }}>
              {GENRES.map((g) => {
                const active = genre === g.value;
                return (
                  <button
                    key={g.value}
                    type="button"
                    onClick={() => setGenre(g.value)}
                    style={{
                      padding: "0.6rem 0.3rem",
                      background: active ? "#6366f126" : "#121212",
                      border: `1px solid ${active ? "#6366f1" : "#2a2a2a"}`,
                      borderRadius: 10,
                      cursor: "pointer",
                      textAlign: "center",
                      color: active ? "#c7d2fe" : "#bbb",
                      transition: "all .15s",
                    }}
                  >
                    <div style={{ fontSize: "1.25rem" }}>{g.emoji}</div>
                    <div style={{ fontSize: "0.72rem", marginTop: 3 }}>{g.label}</div>
                  </button>
                );
              })}
            </div>
          </div>

          <div style={{ marginBottom: "1.75rem" }}>
            <label style={label}>简介（可选）</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="一句话梗概、主要冲突或想看的画面……AI 会据此扩写"
              style={{ ...inputStyle, minHeight: 120, resize: "vertical", lineHeight: 1.6 }}
            />
          </div>

          <button
            type="submit"
            disabled={loading || !title.trim()}
            style={{
              width: "100%",
              padding: "0.85rem",
              background: loading || !title.trim() ? "#2a2a2a" : "linear-gradient(135deg,#6366f1,#8b5cf6)",
              color: loading || !title.trim() ? "#777" : "#fff",
              border: "none",
              borderRadius: 10,
              fontSize: "0.98rem",
              fontWeight: 600,
              cursor: loading || !title.trim() ? "not-allowed" : "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
            }}
          >
            {loading && <Loader2 size={15} className="spin" />}
            {loading ? "创建中…" : "创建并开始策划"}
          </button>
        </form>
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

const label: React.CSSProperties = {
  display: "block",
  marginBottom: "0.45rem",
  color: "#9ca3af",
  fontSize: "0.8rem",
  fontWeight: 600,
};
