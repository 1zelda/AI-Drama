"use client";
/**
 * 本地资产库：参考图 / 视频的上传、打标签、预览、删除。
 * 数据来自 FastAPI（backend/app/api/assets.py）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, api } from "@/lib/api";
import TopNav from "@/components/TopNav";

type Asset = {
  id: string;
  filename: string;
  kind: "image" | "video" | "other";
  url: string;
  tags: string[];
  note: string;
  size: number;
  created_at: number;
};

const KIND_LABEL: Record<string, string> = { image: "图片", video: "视频", other: "其它" };

export default function AssetsPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [tagFilter, setTagFilter] = useState("");
  const [uploadTags, setUploadTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const allTags = Array.from(new Set(assets.flatMap((a) => a.tags))).sort();

  const refresh = useCallback(() => {
    const q = tagFilter ? `?tag=${encodeURIComponent(tagFilter)}` : "";
    api<Asset[]>(`/api/assets/${q}`)
      .then(setAssets)
      .catch((e) => setMsg(`后端不可达：${e.message}`));
  }, [tagFilter]);

  useEffect(refresh, [refresh]);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setMsg("");
    for (const file of Array.from(files)) {
      const form = new FormData();
      form.append("file", file);
      form.append("tags", uploadTags);
      try {
        await api("/api/assets/upload", { method: "POST", body: form });
      } catch (e: any) {
        setMsg(`上传 ${file.name} 失败：${e.message}`);
      }
    }
    setBusy(false);
    if (fileRef.current) fileRef.current.value = "";
    refresh();
  };

  const remove = async (id: string) => {
    try {
      await api(`/api/assets/${id}`, { method: "DELETE" });
      refresh();
    } catch (e: any) {
      setMsg(`删除失败：${e.message}`);
    }
  };

  const toggleTag = (asset: Asset, tag: string) => {
    const tags = asset.tags.includes(tag)
      ? asset.tags.filter((t) => t !== tag)
      : [...asset.tags, tag];
    api(`/api/assets/${asset.id}/tags`, { method: "PUT", body: JSON.stringify({ tags }) }).then(refresh);
  };

  return (
    <main style={{ minHeight: "100vh", background: "#0a0a0a", color: "#eee" }}>
      <TopNav
        title="资产库"
        subtitle={`${assets.length} 个${tagFilter ? ` · 过滤 ${tagFilter}` : ""}`}
        actions={
          <>
            <input
              value={uploadTags}
              onChange={(e) => setUploadTags(e.target.value)}
              placeholder="上传标签，如 character:林晚"
              style={{ background: "#161622", border: "1px solid #333", borderRadius: 8, padding: "0.35rem 0.6rem", color: "#eee", fontSize: "0.76rem", width: 220 }}
            />
            <input ref={fileRef} type="file" multiple accept="image/*,video/*" onChange={(e) => upload(e.target.files)} style={{ display: "none" }} />
            <button
              onClick={() => fileRef.current?.click()}
              disabled={busy}
              style={{ background: "#6366f1", color: "#fff", border: "none", borderRadius: 8, padding: "0.4rem 0.9rem", fontWeight: 600, fontSize: "0.78rem", cursor: busy ? "wait" : "pointer" }}
            >
              {busy ? "上传中…" : "＋ 上传"}
            </button>
            <select
              value={tagFilter}
              onChange={(e) => setTagFilter(e.target.value)}
              style={{ background: "#161622", color: "#eee", border: "1px solid #333", borderRadius: 8, padding: "0.35rem 0.5rem", fontSize: "0.76rem" }}
            >
              <option value="">全部标签</option>
              {allTags.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </>
        }
      />

      {msg && (
        <div style={{ margin: "1rem 1.25rem 0", padding: "0.6rem 0.9rem", background: "#2a1214", border: "1px solid #7f1d1d", borderRadius: 9, color: "#fca5a5", fontSize: "0.8rem" }}>
          ⚠️ {msg}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 14, padding: 20 }}>
        {assets.map((a) => (
          <div key={a.id} style={{ background: "#141420", border: "1px solid #26263a", borderRadius: 12, overflow: "hidden" }}>
            <div style={{ aspectRatio: "16 / 9", background: "#0d0d15", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
              {a.kind === "image" ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={`${API_BASE}${a.url}`} alt={a.filename} style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} />
              ) : a.kind === "video" ? (
                <video src={`${API_BASE}${a.url}`} style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} muted />
              ) : (
                <span style={{ color: "#555" }}>{KIND_LABEL[a.kind]}</span>
              )}
            </div>
            <div style={{ padding: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{a.filename}</div>
              <div style={{ fontSize: 11, color: "#666", margin: "2px 0 8px" }}>
                {(a.size / 1024).toFixed(0)} KB · {new Date(a.created_at * 1000).toLocaleDateString()}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
                {a.tags.map((t) => (
                  <button
                    key={t}
                    onClick={() => toggleTag(a, t)}
                    title="点击移除标签"
                    style={{ background: "#6366f122", color: "#a5b4fc", border: "1px solid #6366f144", borderRadius: 5, fontSize: 11, padding: "1px 7px", cursor: "pointer" }}
                  >
                    {t} ×
                  </button>
                ))}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <a href={`${API_BASE}${a.url}`} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: "#8ab4ff", textDecoration: "none" }}>
                  原图
                </a>
                <button
                  onClick={() => remove(a.id)}
                  style={{ marginLeft: "auto", background: "transparent", color: "#ef4444", border: "1px solid #ef444455", borderRadius: 6, fontSize: 12, padding: "2px 10px", cursor: "pointer" }}
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        ))}
        {!assets.length && !msg && <div style={{ color: "#555", fontSize: 13 }}>还没有资产，先上传几张角色参考图吧</div>}
      </div>
    </main>
  );
}
