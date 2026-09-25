"use client"
import { useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { ArrowLeft, Settings } from "lucide-react"

export default function NewProject() {
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [genre, setGenre] = useState("drama")
  const [loading, setLoading] = useState(false)
  const router = useRouter()

  const genres = [
    { value: "drama", label: "Drama", emoji: "🎭" },
    { value: "romance", label: "Romance", emoji: "💕" },
    { value: "action", label: "Action", emoji: "💥" },
    { value: "comedy", label: "Comedy", emoji: "😂" },
    { value: "horror", label: "Horror", emoji: "👻" },
    { value: "scifi", label: "Sci-Fi", emoji: "🚀" },
    { value: "fantasy", label: "Fantasy", emoji: "🧙" },
    { value: "thriller", label: "Thriller", emoji: "🔪" },
  ]

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return
    setLoading(true)
    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, description, genre }),
      })
      const project = await res.json()
      router.push(`/project/${project.id}`)
    } catch (err) {
      console.error(err)
      alert("Failed to create project")
    } finally {
      setLoading(false)
    }
  }

  return (
    <main style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <header style={{ padding: "1.5rem 2rem", borderBottom: "1px solid #2a2a2a", display: "flex", alignItems: "center", gap: "1rem" }}>
        <Link href="/" style={{ color: "#888", textDecoration: "none", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.9rem" }}>
          <ArrowLeft size={16} /> Back
        </Link>
        <Link href="/settings" style={{ color: "#888", textDecoration: "none", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.9rem" }}>
          <Settings size={16} /> Settings
        </Link>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 700 }}>New Project</h1>
      </header>

      <div style={{ maxWidth: 640, margin: "0 auto", padding: "3rem 2rem" }}>
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: "1.5rem" }}>
            <label style={{ display: "block", marginBottom: "0.5rem", color: "#aaa", fontSize: "0.9rem" }}>Title *</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Enter your story title..."
              style={{ width: "100%", padding: "0.875rem", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 10, color: "#fff", fontSize: "1rem", outline: "none" }}
              required
            />
          </div>

          <div style={{ marginBottom: "1.5rem" }}>
            <label style={{ display: "block", marginBottom: "0.5rem", color: "#aaa", fontSize: "0.9rem" }}>Genre</label>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "0.75rem" }}>
              {genres.map((g) => (
                <button
                  key={g.value}
                  type="button"
                  onClick={() => setGenre(g.value)}
                  style={{
                    padding: "0.75rem",
                    background: genre === g.value ? "#6366f1" : "#141414",
                    border: "1px solid" + (genre === g.value ? "#6366f1" : "#2a2a2a"),
                    borderRadius: 10,
                    cursor: "pointer",
                    textAlign: "center",
                    color: "#fff",
                    transition: "all 0.2s",
                  }}
                >
                  <div style={{ fontSize: "1.5rem" }}>{g.emoji}</div>
                  <div style={{ fontSize: "0.75rem", marginTop: "0.25rem" }}>{g.label}</div>
                </button>
              ))}
            </div>
          </div>

          <div style={{ marginBottom: "2rem" }}>
            <label style={{ display: "block", marginBottom: "0.5rem", color: "#aaa", fontSize: "0.9rem" }}>Description (optional)</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Brief story description or key plot points..."
              style={{ width: "100%", padding: "0.875rem", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 10, color: "#fff", fontSize: "1rem", minHeight: 120, resize: "vertical", outline: "none" }}
            />
          </div>

          <button
            type="submit"
            disabled={loading || !title.trim()}
            style={{
              width: "100%",
              padding: "1rem",
              background: loading || !title.trim() ? "#333" : "linear-gradient(135deg, #6366f1, #8b5cf6)",
              color: "#fff",
              border: "none",
              borderRadius: 10,
              fontSize: "1.1rem",
              fontWeight: 600,
              cursor: loading || !title.trim() ? "not-allowed" : "pointer",
            }}
          >
            {loading ? "Creating..." : "Start Planning"}
          </button>
        </form>
      </div>
    </main>
  )
}