import React, { useState } from "react"

interface Message {
  role: "user" | "assistant"
  content: string
}

export default function ChatDialog({ projectId, onProjectUpdate }: { projectId: string; onProjectUpdate: (p?: any) => void }) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)

  const sendMessage = async () => {
    if (!input.trim() || loading) return
    const userMsg = input.trim()
    setInput("")
    setMessages(prev => [...prev, { role: "user", content: userMsg }])
    setLoading(true)
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId, message: userMsg })
      })
      const data = await res.json()
      setMessages(prev => [...prev, { role: "assistant", content: data.reply }])
      if (data.updated_project) onProjectUpdate(data.updated_project)
    } catch (err) {
      setMessages(prev => [...prev, { role: "assistant", content: "Sorry, something went wrong." }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{display: "flex", flexDirection: "column", height: "400px", background: "#111", borderRadius: "12px", border: "1px solid #333"}}>
      <div style={{flex: 1, overflowY: "auto", padding: "1rem"}}>
        {messages.length === 0 && <div style={{color: "#666", textAlign: "center", padding: "2rem"}}>Ask me anything about your drama - plot, characters, episodes!</div>}
        {messages.map((msg, i) => (
          <div key={i} style={{marginBottom: "1rem"}}>
            <strong style={{color: msg.role === "user" ? "#6366f1" : "#4ade80"}}>{msg.role === "user" ? "You" : "AI"}:</strong>
            <p style={{margin: "0.25rem 0 0", whiteSpace: "pre-wrap", color: "#ccc"}}>{msg.content}</p>
          </div>
        ))}
        {loading && <div style={{color: "#666"}}>Thinking...</div>}
      </div>
      <div style={{display: "flex", padding: "0.75rem", borderTop: "1px solid #333"}}>
        <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === "Enter" && sendMessage()} placeholder="Type your message..." style={{flex: 1, padding: "0.5rem", background: "#1a1a1a", border: "1px solid #333", borderRadius: "8px 0 0 8px", color: "#fff"}} />
        <button onClick={sendMessage} disabled={loading} style={{padding: "0.5rem 1rem", background: "#6366f1", color: "#fff", border: "none", borderRadius: "0 8px 8px 0", cursor: "pointer"}}>Send</button>
      </div>
    </div>
  )
}
