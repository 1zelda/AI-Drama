"use client"
import { useState, useEffect, useRef } from "react"
import { useParams, useRouter } from "next/navigation"
import Link from "next/link"
import { ArrowLeft, Send, Sparkles, CheckCircle, XCircle, Play, Settings } from "lucide-react"

export default function ProjectPage() {
  const params = useParams()
  const router = useRouter()
  const projectId = params.id as string

  const [project, setProject] = useState<any>(null)
  const [activeTab, setActiveTab] = useState("chat")
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [approvals, setApprovals] = useState<any[]>([])
  const [generating, setGenerating] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    loadProject()
    loadApprovals()
  }, [projectId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const loadProject = async () => {
    try {
      const res = await fetch(`/api/projects/${projectId}`)
      const data = await res.json()
      setProject(data)
      if (data.plan?.synopsis) {
        const chars = (data.plan.characters || []).map((c: any) => c.name).join(", ")
        const eps = data.plan.episodes?.length || 0
        setMessages([{ role: "assistant", content: `Plan complete for "${data.title}"!\n\n${data.plan.synopsis}\n\nCharacters: ${chars}\nEpisodes: ${eps}\n\nHow would you like to proceed?` }])
      }
    } catch (e) { console.error(e) }
  }

  const loadApprovals = async () => {
    try {
      const res = await fetch(`/api/projects/${projectId}/approvals`)
      const data = await res.json()
      setApprovals(data)
    } catch (e) {}
  }

  const sendChat = async () => {
    if (!input.trim() || loading) return
    const userMsg = input.trim()
    setInput("")
    setMessages((prev) => [...prev, { role: "user", content: userMsg }])
    setLoading(true)
    try {
      const res = await fetch(`/api/projects/${projectId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMsg, context: project }),
      })
      const data = await res.json()
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }])
    } catch (err) {
      setMessages((prev) => [...prev, { role: "assistant", content: "Sorry, something went wrong." }])
    } finally {
      setLoading(false)
    }
  }

  const handlePlan = async () => {
    setGenerating(true)
    try {
      const res = await fetch(`/api/projects/${projectId}/plan`, { method: "POST" })
      const data = await res.json()
      setProject(data)
      setMessages([{ role: "assistant", content: `Plan complete for "${data.title}"!\n\n${data.plan?.synopsis || "Plan generated"}` }])
    } catch (e) {
      alert("Failed to generate plan")
    } finally {
      setGenerating(false)
    }
  }

  const handleApprove = async (itemId: string) => {
    await fetch(`/api/projects/${projectId}/approvals/${itemId}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "approve" }),
    })
    loadApprovals()
  }

  const handleReject = async (itemId: string, feedback: string) => {
    await fetch(`/api/projects/${projectId}/approvals/${itemId}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "reject", feedback }),
    })
    loadApprovals()
  }

  if (!project) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>⏳</div>
          <p style={{ color: "#888" }}>Loading project...</p>
        </div>
      </div>
    )
  }

  const pendingApprovals = approvals.filter((a) => a.status === "pending")

  return (
    <main style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <header style={{ padding: "1rem 2rem", borderBottom: "1px solid #2a2a2a", display: "flex", alignItems: "center", gap: "1rem" }}>
        <Link href="/" style={{ color: "#888", textDecoration: "none", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.9rem" }}>
          <ArrowLeft size={16} /> Back
        </Link>
        <Link href="/settings" style={{ color: "#888", textDecoration: "none", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.9rem" }}>
          <Settings size={16} /> Settings
        </Link>
        <h1 style={{ fontSize: "1.1rem", fontWeight: 700, flex: 1 }}>{project.title}</h1>
        <span style={{ fontSize: "0.75rem", padding: "0.25rem 0.75rem", background: project.status === "completed" ? "#22c55e22" : "#6366f122", color: project.status === "completed" ? "#22c55e" : "#6366f1", borderRadius: 20 }}>
          {project.status || "planning"}
        </span>
      </header>

      <div style={{ display: "flex", gap: "0", borderBottom: "1px solid #2a2a2a", padding: "0 2rem" }}>
        {[
          { id: "chat", label: "AI Planning", icon: Sparkles },
          { id: "board", label: `Approval Board${pendingApprovals.length > 0 ? ` (${pendingApprovals.length})` : ""}`, icon: CheckCircle },
          { id: "editor", label: "Workflow Editor", icon: Play },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: "1rem 1.5rem",
              background: "none",
              border: "none",
              borderBottom: activeTab === tab.id ? "2px solid #6366f1" : "2px solid transparent",
              color: activeTab === tab.id ? "#6366f1" : "#888",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              fontSize: "0.95rem",
              fontWeight: activeTab === tab.id ? 600 : 400,
            }}
          >
            <tab.icon size={16} />
            {tab.label}
          </button>
        ))}
      </div>

      <div style={{ padding: "2rem" }}>
        {activeTab === "chat" && (
          <ChatPanel
            messages={messages}
            input={input}
            setInput={setInput}
            loading={loading}
            generating={generating}
            onSend={sendChat}
            onPlan={handlePlan}
          />
        )}
        {activeTab === "board" && (
          <BoardPanel
            approvals={approvals}
            onApprove={handleApprove}
            onReject={handleReject}
            onGenerate={handlePlan}
          />
        )}
        {activeTab === "editor" && <EditorPanel project={project} />}
      </div>
    </main>
  )
}

function ChatPanel({ messages, input, setInput, loading, generating, onSend, onPlan }: {
  messages: { role: string; content: string }[]
  input: string
  setInput: (v: string) => void
  loading: boolean
  generating: boolean
  onSend: () => void
  onPlan: () => void
}) {
  const messagesEndRef = useRef<HTMLDivElement>(null)
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }) }, [messages])

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 200px)", maxWidth: 900, margin: "0 auto" }}>
      <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1.5rem", flexWrap: "wrap" }}>
        <button onClick={onPlan} disabled={generating} style={{ padding: "0.6rem 1.2rem", background: generating ? "#333" : "linear-gradient(135deg, #6366f1, #8b5cf6)", color: "#fff", border: "none", borderRadius: 8, cursor: generating ? "not-allowed" : "pointer", fontSize: "0.9rem", fontWeight: 500, display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <Sparkles size={16} />{generating ? "Generating..." : "Auto-Plan Project"}
        </button>
        <button style={{ padding: "0.6rem 1.2rem", background: "#141414", color: "#ccc", border: "1px solid #2a2a2a", borderRadius: 8, cursor: "pointer", fontSize: "0.9rem" }}>👤 Generate Characters</button>
        <button style={{ padding: "0.6rem 1.2rem", background: "#141414", color: "#ccc", border: "1px solid #2a2a2a", borderRadius: 8, cursor: "pointer", fontSize: "0.9rem" }}>📺 Generate Episodes</button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", paddingRight: "0.5rem", marginBottom: "1rem" }}>
        {messages.length === 0 && (
          <div style={{ textAlign: "center", padding: "3rem", color: "#555" }}>
            <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>💬</div>
            <p>Start chatting to plan your drama</p>
            <p style={{ fontSize: "0.85rem", marginTop: "0.5rem" }}>Ask about plot, characters, episodes, or visual style</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{ marginBottom: "1.25rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.35rem" }}>
              <span style={{ fontSize: "0.75rem", color: msg.role === "user" ? "#6366f1" : "#4ade80", fontWeight: 600 }}>
                {msg.role === "user" ? "You" : "AI Director"}
              </span>
            </div>
            <div style={{
              padding: "1rem 1.25rem",
              background: msg.role === "user" ? "#1a1a2e" : "#141414",
              borderRadius: msg.role === "user" ? "12px 12px 4px 12px" : "12px 12px 12px 4px",
              border: `1px solid ${msg.role === "user" ? "#6366f133" : "#2a2a2a"}`,
              whiteSpace: "pre-wrap",
              fontSize: "0.95rem",
              lineHeight: 1.6,
              color: "#ddd",
            }}>{msg.content}</div>
          </div>
        ))}
        {loading && (
          <div style={{ marginBottom: "1rem" }}>
            <div style={{ fontSize: "0.75rem", color: "#4ade80", fontWeight: 600, marginBottom: "0.35rem" }}>AI Director</div>
            <div style={{ padding: "1rem 1.25rem", background: "#141414", borderRadius: "12px 12px 12px 4px", border: "1px solid #2a2a2a", color: "#888" }}>Thinking...</div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div style={{ display: "flex", gap: "0.75rem", padding: "1rem", background: "#141414", borderRadius: 12, border: "1px solid #2a2a2a" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && onSend()}
          placeholder="Ask about plot, characters, episodes, visual style..."
          style={{ flex: 1, padding: "0.75rem", background: "transparent", border: "none", color: "#fff", fontSize: "0.95rem", outline: "none" }}
        />
        <button onClick={onSend} disabled={!input.trim() || loading} style={{ padding: "0.75rem 1.25rem", background: input.trim() && !loading ? "#6366f1" : "#333", color: "#fff", border: "none", borderRadius: 8, cursor: input.trim() && !loading ? "pointer" : "not-allowed" }}>
          <Send size={16} />
        </button>
      </div>
    </div>
  )
}

function BoardPanel({ approvals, onApprove, onReject, onGenerate }: {
  approvals: any[]
  onApprove: (itemId: string) => void
  onReject: (itemId: string, feedback: string) => void
  onGenerate: () => void
}) {
  const [feedback, setFeedback] = useState<Record<string, string>>({})
  const pending = approvals.filter((a) => a.status === "pending")
  const done = approvals.filter((a) => a.status !== "pending")

  if (approvals.length === 0) {
    return (
      <div style={{ textAlign: "center", padding: "4rem", maxWidth: 600, margin: "0 auto" }}>
        <div style={{ fontSize: "4rem", marginBottom: "1rem" }}>📋</div>
        <h2 style={{ fontSize: "1.5rem", fontWeight: 700, marginBottom: "0.75rem" }}>No Approval Items Yet</h2>
        <p style={{ color: "#888", marginBottom: "2rem" }}>Generate your drama plan first, then approval items will appear here.</p>
        <button onClick={onGenerate} style={{ padding: "0.75rem 2rem", background: "#6366f1", color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", fontWeight: 600 }}>Generate Plan</button>
      </div>
    )
  }

  return (
    <div>
      <h2 style={{ fontSize: "1.25rem", fontWeight: 700, marginBottom: "1.5rem" }}>
        Approval Board
        <span style={{ fontSize: "0.85rem", color: "#888", fontWeight: 400, marginLeft: "0.75rem" }}>{pending.length} pending, {done.length} done</span>
      </h2>

      {pending.length > 0 && (
        <div style={{ marginBottom: "2rem" }}>
          <h3 style={{ fontSize: "0.9rem", color: "#f59e0b", fontWeight: 600, marginBottom: "1rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Pending Review</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "1rem" }}>
            {pending.map((item) => (
              <div key={item.id} style={{ padding: "1.25rem", background: "#141414", borderRadius: 12, border: "1px solid #f59e0b33" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                  <span style={{ fontSize: "0.75rem", padding: "0.2rem 0.6rem", background: "#f59e0b22", color: "#f59e0b", borderRadius: 20 }}>{item.stage}</span>
                  <span style={{ fontSize: "0.75rem", color: "#888" }}>{item.item_type}</span>
                </div>
                <h4 style={{ fontSize: "1rem", fontWeight: 600, marginBottom: "0.5rem" }}>{item.title}</h4>
                <p style={{ fontSize: "0.85rem", color: "#888", marginBottom: "1rem" }}>{item.description}</p>
                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <input
                    value={feedback[item.id] || ""}
                    onChange={(e) => setFeedback({ ...feedback, [item.id]: e.target.value })}
                    placeholder="Add feedback (optional)..."
                    style={{ flex: 1, padding: "0.5rem", background: "#1a1a1a", border: "1px solid #2a2a2a", borderRadius: 6, color: "#fff", fontSize: "0.8rem" }}
                  />
                  <button onClick={() => onReject(item.id, feedback[item.id] || "")} style={{ padding: "0.5rem 0.75rem", background: "#dc2626", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: "0.8rem" }}>
                    <XCircle size={14} />
                  </button>
                  <button onClick={() => onApprove(item.id)} style={{ padding: "0.5rem 0.75rem", background: "#22c55e", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontSize: "0.8rem" }}>
                    <CheckCircle size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {done.length > 0 && (
        <div>
          <h3 style={{ fontSize: "0.9rem", color: "#888", fontWeight: 600, marginBottom: "1rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Completed</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "1rem" }}>
            {done.map((item) => (
              <div key={item.id} style={{ padding: "1rem", background: "#141414", borderRadius: 12, border: "1px solid #2a2a2a", opacity: 0.7 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                  <span style={{ fontSize: "0.75rem", color: item.status === "approved" ? "#22c55e" : "#dc2626" }}>
                    {item.status === "approved" ? "✓ Approved" : "✗ Rejected"}
                  </span>
                  <span style={{ fontSize: "0.75rem", color: "#666" }}>{item.stage}</span>
                </div>
                <h4 style={{ fontSize: "0.95rem", fontWeight: 600 }}>{item.title}</h4>
                {item.feedback && <p style={{ fontSize: "0.8rem", color: "#888", marginTop: "0.5rem" }}>Feedback: {item.feedback}</p>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function EditorPanel({ project }: { project: any }) {
  return (
    <div style={{ textAlign: "center", padding: "4rem" }}>
      <div style={{ fontSize: "4rem", marginBottom: "1rem" }}>🎛️</div>
      <h2 style={{ fontSize: "1.5rem", fontWeight: 700, marginBottom: "0.75rem" }}>Workflow Editor</h2>
      <p style={{ color: "#888", maxWidth: 500, margin: "0 auto 2rem" }}>
        Drag-and-drop workflow editor coming soon. Configure ComfyUI nodes, set parameters, and define generation pipelines.
      </p>
      <div style={{ display: "inline-block", padding: "1rem 2rem", background: "#141414", borderRadius: 12, border: "1px solid #2a2a2a", textAlign: "left", maxWidth: 500 }}>
        <h4 style={{ marginBottom: "0.75rem", color: "#6366f1" }}>Available Workflows</h4>
        <div style={{ fontSize: "0.85rem", color: "#888" }}>
          <div style={{ padding: "0.5rem 0", borderBottom: "1px solid #2a2a2a" }}>🎨 Character Sheet Generator</div>
          <div style={{ padding: "0.5rem 0", borderBottom: "1px solid #2a2a2a" }}>🏞️ Scene/Storyboard Generator</div>
          <div style={{ padding: "0.5rem 0" }}>🎬 Image-to-Video (Wan 2.2)</div>
        </div>
      </div>
    </div>
  )
}