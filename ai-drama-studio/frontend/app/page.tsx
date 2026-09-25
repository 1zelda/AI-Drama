"use client"
import { useState, useEffect } from "react"
import Link from "next/link"
import { Plus, Play, MessageSquare, LayoutGrid, Settings } from "lucide-react"

export default function Home() {
  const [projects, setProjects] = useState<any[]>([])

  useEffect(() => {
    fetch("/api/projects")
      .then(r => r.json())
      .then(data => setProjects(data))
      .catch(() => {})
  }, [])

  return (
    <main style={{ minHeight: "100vh", background: "linear-gradient(135deg, #0a0a0a 0%, #1a1a2e 100%)" }}>
      {/* Header */}
      <header style={{ padding: "1.5rem 2rem", display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #2a2a2a" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <div style={{ width: 36, height: 36, background: "linear-gradient(135deg, #6366f1, #8b5cf6)", borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center", fontSize: "1.2rem" }}>
            🎬
          </div>
          <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>AI Drama Studio</span>
        </div>
        <Link href="/new" style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.6rem 1.2rem", background: "#6366f1", color: "#fff", borderRadius: 8, textDecoration: "none", fontSize: "0.9rem", fontWeight: 500 }}>
          <Plus size={16} /> New Project
        </Link>
        <Link href="/settings" style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.6rem 1rem", background: "#1a1a1a", color: "#fff", borderRadius: 8, textDecoration: "none", fontSize: "0.9rem", border: "1px solid #2a2a2a" }}>
          <Settings size={16} /> Settings
        </Link>
      </header>

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "3rem 2rem" }}>
        {/* Hero */}
        <div style={{ textAlign: "center", marginBottom: "4rem" }}>
          <h1 style={{ fontSize: "3rem", fontWeight: 800, marginBottom: "1rem", background: "linear-gradient(135deg, #6366f1, #a78bfa, #f472b6)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
            Create AI Dramas
          </h1>
          <p style={{ fontSize: "1.2rem", color: "#888", maxWidth: 600, margin: "0 auto 2rem" }}>
            Input a title, discuss with AI, and generate complete video series with character consistency
          </p>
          <div style={{ display: "flex", justifyContent: "center", gap: "1rem" }}>
            <Link href="/new" style={{ padding: "0.8rem 2rem", background: "#6366f1", color: "#fff", borderRadius: 8, textDecoration: "none", fontWeight: 600 }}>
              Start Creating
            </Link>
          </div>
        </div>

        {/* Features */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "1.5rem", marginBottom: "4rem" }}>
          {[
            { icon: "💬", title: "AI Planning", desc: "Chat-based story, character & episode planning" },
            { icon: "👤", title: "Character Consistency", desc: "IP-Adapter powered reference image management" },
            { icon: "🎬", title: "Video Generation", desc: "ComfyUI + cloud API video generation pipeline" },
          ].map((f, i) => (
            <div key={i} style={{ padding: "1.5rem", background: "#141414", borderRadius: 16, border: "1px solid #2a2a2a" }}>
              <div style={{ fontSize: "2rem", marginBottom: "0.75rem" }}>{f.icon}</div>
              <h3 style={{ fontSize: "1.1rem", fontWeight: 600, marginBottom: "0.5rem" }}>{f.title}</h3>
              <p style={{ fontSize: "0.85rem", color: "#888" }}>{f.desc}</p>
            </div>
          ))}
        </div>

        {/* Projects */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "1.5rem", fontWeight: 700 }}>Your Projects</h2>
          {projects.length > 0 && (
            <span style={{ color: "#888", fontSize: "0.9rem" }}>{projects.length} project{projects.length !== 1 ? "s" : ""}</span>
          )}
        </div>

        {projects.length === 0 ? (
          <div style={{ textAlign: "center", padding: "4rem", background: "#141414", borderRadius: 16, border: "2px dashed #2a2a2a" }}>
            <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>🎭</div>
            <p style={{ color: "#888", marginBottom: "1.5rem" }}>No projects yet. Create your first AI drama!</p>
            <Link href="/new" style={{ padding: "0.75rem 2rem", background: "#6366f1", color: "#fff", borderRadius: 8, textDecoration: "none", fontWeight: 600 }}>
              Create Project
            </Link>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "1.5rem" }}>
            {projects.map((p) => (
              <Link href={`/project/${p.id}`} key={p.id} style={{ display: "block", padding: "1.5rem", background: "#141414", borderRadius: 16, border: "1px solid #2a2a2a", textDecoration: "none", transition: "border-color 0.2s" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.75rem" }}>
                  <h3 style={{ fontSize: "1.1rem", fontWeight: 600, flex: 1 }}>{p.title}</h3>
                  <span style={{ fontSize: "0.75rem", padding: "0.25rem 0.75rem", background: p.status === "completed" ? "#22c55e22" : "#6366f122", color: p.status === "completed" ? "#22c55e" : "#6366f1", borderRadius: 20, fontWeight: 500 }}>
                    {p.status || "planning"}
                  </span>
                </div>
                {p.description && <p style={{ fontSize: "0.85rem", color: "#888", marginBottom: "1rem" }}>{p.description}</p>}
                <div style={{ display: "flex", gap: "1.5rem", fontSize: "0.8rem", color: "#666" }}>
                  <span>👤 {p.characters?.length || 0} chars</span>
                  <span>📺 {p.episodes?.length || 0} eps</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </main>
  )
}