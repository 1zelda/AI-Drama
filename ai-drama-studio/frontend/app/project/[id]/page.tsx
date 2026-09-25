"use client"
import { useState, useEffect, useRef } from "react"
import { useParams } from "next/navigation"
import Link from "next/link"
import { Send, Sparkles, CheckCircle, XCircle, Play, ExternalLink, Loader2 } from "lucide-react"
import TopNav from "@/components/TopNav"

export default function ProjectPage() {
  const params = useParams()
  const projectId = params.id as string

  const [project, setProject] = useState<any>(null)
  const [activeTab, setActiveTab] = useState("chat")
  const [messages, setMessages] = useState<{role: string; content: string}[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [approvals, setApprovals] = useState<any[]>([])
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState("")
  const [notFound, setNotFound] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    loadProject()
    loadApprovals()
  }, [projectId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const loadProject = async () => {
    try {
      const res = await fetch(`/api/projects/${projectId}`, { cache: "no-store" })
      const data = await res.json()
      if (res.status === 404) { setNotFound(true); return }
      if (!res.ok) throw new Error(data?.error || `加载失败（HTTP ${res.status}）`)
      setError("")
      setProject(data)
      if (data.plan?.synopsis) {
        const chars = (data.plan.characters || []).map((c: any) => c.name).join("、")
        const eps = data.plan.episodes?.length || 0
        setMessages([{ role: "assistant", content: `《${data.title}》策划已就绪\n\n${data.plan.synopsis}\n\n角色：${chars || "（暂无）"}\n集数：${eps}\n\n想调整哪一部分？直接说，或点「AI 自动策划」重新生成一版。` }])
      }
    } catch (e: any) {
      setError(e.message || "加载项目失败")
    }
  }

  const loadApprovals = async () => {
    try {
      const res = await fetch(`/api/projects/${projectId}/approvals`, { cache: "no-store" })
      const data = await res.json()
      setApprovals(Array.isArray(data) ? data : [])
    } catch (e) { /* 审批列表失败不阻塞主流程 */ }
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
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`)
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply || "（没有返回内容）" }])
    } catch (err: any) {
      setMessages((prev) => [...prev, { role: "assistant", content: `⚠️ ${err.message || "请求失败"}` }])
    } finally {
      setLoading(false)
    }
  }

  const handlePlan = async () => {
    setGenerating(true)
    setError("")
    try {
      const res = await fetch(`/api/projects/${projectId}/plan`, { method: "POST" })
      const data = await res.json()
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`)
      setProject(data)
      setMessages([{ role: "assistant", content: `《${data.title}》策划完成\n\n${data.plan?.synopsis || "已生成策划"}` }])
    } catch (e: any) {
      setError(`自动策划失败：${e.message}（检查「设置」里的 LLM Key 是否已填写并保存）`)
    } finally {
      setGenerating(false)
    }
  }

  const actApproval = async (itemId: string, action: "approve" | "reject", feedback = "") => {
    try {
      const res = await fetch(`/api/projects/${projectId}/approvals/${itemId}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, feedback }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`)
      await loadApprovals()
    } catch (e: any) {
      setError(`审批操作失败：${e.message}`)
    }
  }

  const handleApprove = (itemId: string) => actApproval(itemId, "approve")
  const handleReject = (itemId: string, feedback: string) => actApproval(itemId, "reject", feedback)

  if (notFound) {
    return (
      <main style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
        <TopNav title="项目不存在" />
        <div style={{ textAlign: "center", padding: "4rem 1rem" }}>
          <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>🔍</div>
          <p style={{ color: "#8b8b8b", marginBottom: "1.5rem", fontSize: "0.9rem" }}>
            没有找到项目 <code style={{ color: "#a5b4fc" }}>{projectId}</code>，可能已被删除。
          </p>
          <Link href="/" style={{ padding: "0.6rem 1.4rem", background: "#6366f1", color: "#fff", borderRadius: 9, textDecoration: "none", fontSize: "0.85rem" }}>
            返回首页
          </Link>
        </div>
      </main>
    )
  }

  if (!project) {
    return (
      <main style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
        <TopNav title="加载中" />
        <div style={{ textAlign: "center", padding: "4rem 1rem", color: "#8b8b8b" }}>
          {error ? (
            <>
              <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>⚠️</div>
              <p style={{ fontSize: "0.9rem" }}>{error}</p>
            </>
          ) : (
            <>
              <Loader2 size={28} className="spin" style={{ margin: "0 auto 1rem", color: "#6366f1" }} />
              <p style={{ fontSize: "0.9rem" }}>正在加载项目…</p>
            </>
          )}
        </div>
        <style jsx global>{`.spin{animation:spin .9s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      </main>
    )
  }

  const pendingApprovals = approvals.filter((a) => a.status === "pending")

  return (
    <main style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav
        title={project.title}
        subtitle={project.genre ? `题材 ${project.genre}` : undefined}
        actions={
          <>
            <span style={{
              fontSize: "0.72rem", padding: "0.2rem 0.6rem", borderRadius: 999,
              background: project.status === "completed" ? "#22c55e22" : "#6366f122",
              color: project.status === "completed" ? "#22c55e" : "#818cf8",
            }}>
              {project.status || "planning"}
            </span>
            <Link href="/pipeline" style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              padding: "0.4rem 0.75rem", background: "#171717",
              border: "1px solid #2a2a2a", borderRadius: 9,
              color: "#ddd", textDecoration: "none", fontSize: "0.78rem",
            }}>
              <Play size={13} /> 去流水线
            </Link>
          </>
        }
      />

      {error && (
        <div style={{
          margin: "1rem 1.25rem 0", padding: "0.7rem 1rem", background: "#2a1214",
          border: "1px solid #7f1d1d", borderRadius: 10, color: "#fca5a5", fontSize: "0.82rem",
        }}>
          ⚠️ {error}
        </div>
      )}

      <div style={{ display: "flex", gap: "0", borderBottom: "1px solid #2a2a2a", padding: "0 2rem" }}>
        {[
          { id: "chat", label: "AI 策划", icon: Sparkles },
          { id: "board", label: `审批看板${pendingApprovals.length > 0 ? ` (${pendingApprovals.length})` : ""}`, icon: CheckCircle },
          { id: "editor", label: "生成编排", icon: Play },
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

function ChatPanel({ messages, input, setInput, loading, generating, onSend, onPlan }: any) {
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
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
        {messages.map((msg: any, i: number) => (
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

function BoardPanel({ approvals, onApprove, onReject, onGenerate }: any) {
  const [feedback, setFeedback] = useState<Record<string, string>>({})
  const pending = approvals.filter((a: any) => a.status === "pending")
  const done = approvals.filter((a: any) => a.status !== "pending")

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
            {pending.map((item: any) => (
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
            {done.map((item: any) => (
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

function EditorPanel({ project }: any) {
  return (
    <div style={{ textAlign: "center", padding: "3rem 1rem" }}>
      <div style={{ fontSize: "3.2rem", marginBottom: "1rem" }}>🎛️</div>
      <h2 style={{ fontSize: "1.25rem", fontWeight: 700, marginBottom: "0.6rem" }}>生成编排</h2>
      <p style={{ color: "#8b8b8b", maxWidth: 520, margin: "0 auto 1.75rem", fontSize: "0.88rem", lineHeight: 1.7 }}>
        可视化 DAG 画布在「流水线」页：可选工作流、编辑每个节点的模型/提示词/尺寸、
        一键运行并实时看进度与产物。角色定妆照与参考图请先在「资产库」准备。
      </p>
      <div style={{ display: "flex", gap: "0.6rem", justifyContent: "center", flexWrap: "wrap" }}>
        <Link href="/pipeline" style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "0.6rem 1.2rem", background: "#6366f1", color: "#fff",
          borderRadius: 9, textDecoration: "none", fontSize: "0.85rem", fontWeight: 600,
        }}>
          <Play size={14} /> 打开流水线画布
        </Link>
        <Link href="/assets" style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "0.6rem 1.2rem", background: "#171717", color: "#ddd",
          border: "1px solid #2a2a2a", borderRadius: 9, textDecoration: "none", fontSize: "0.85rem",
        }}>
          资产库 <ExternalLink size={13} />
        </Link>
      </div>

      <div style={{
        display: "inline-block", marginTop: "1.75rem", padding: "1rem 1.4rem",
        background: "#121212", borderRadius: 12, border: "1px solid #232323",
        textAlign: "left", maxWidth: 520, width: "100%",
      }}>
        <div style={{ marginBottom: "0.6rem", color: "#818cf8", fontSize: "0.8rem", fontWeight: 600 }}>
          当前项目状态
        </div>
        <div style={{ fontSize: "0.82rem", color: "#8b8b8b", lineHeight: 1.9 }}>
          <div>角色：{project?.characters?.length || 0} 个</div>
          <div>分集：{project?.episodes?.length || 0} 集</div>
          <div>分镜：{project?.storyboard?.length || 0} 组</div>
          {!project?.plan?.synopsis && (
            <div style={{ color: "#f59e0b", marginTop: "0.4rem" }}>
              ⚠️ 还没有策划结果，先回「AI 策划」点「AI 自动策划」
            </div>
          )}
        </div>
      </div>
    </div>
  )
}