"use client"
import { useState, useEffect } from "react"
import Link from "next/link"
import { ArrowLeft, Save, CheckCircle, Loader2, Zap } from "lucide-react"

interface Settings {
  llm_provider: "openai" | "deepseek" | "custom"
  api_key: string
  base_url: string
  model: string
  comfyui_url: string
  comfyui_available: boolean
  kling_api_key: string
  kling_api_url: string
  seedance_api_key: string
  veo_api_key: string
}

export default function SettingsPage() {
  const [s, setS] = useState<Settings>({
    llm_provider: "deepseek",
    api_key: "",
    base_url: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
    comfyui_url: "http://localhost:8188",
    comfyui_available: false,
    kling_api_key: "",
    kling_api_url: "https://api.klingai.com/v1",
    seedance_api_key: "",
    veo_api_key: "",
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)
  const [checking, setChecking] = useState(false)

  useEffect(() => {
    fetch("/api/settings")
      .then(r => r.json())
      .then(data => { setS(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const update = (key: keyof Settings, value: string) => {
    setS(prev => ({ ...prev, [key]: value }))
    setSaved(false)
  }

  const handleSave = async () => {
    setSaving(true); setSaved(false)
    try {
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(s),
      })
      setSaved(true); setTimeout(() => setSaved(false), 2000)
    } finally { setSaving(false) }
  }

  const checkComfyUI = async () => {
    setChecking(true)
    try {
      const res = await fetch("/api/settings/check")
      const data = await res.json()
      setS(prev => ({ ...prev, comfyui_available: data.available }))
    } finally { setChecking(false) }
  }

  const providers = [
    { id: "deepseek" as const, label: "DeepSeek", url: "https://api.deepseek.com/v1", model: "deepseek-chat", hint: "Free tier" },
    { id: "openai" as const, label: "OpenAI", url: "https://api.openai.com/v1", model: "gpt-4o", hint: "GPT-4o" },
    { id: "custom" as const, label: "Custom", url: "", model: "", hint: "Any OpenAI-compatible" },
  ]
  const cur = providers.find(p => p.id === s.llm_provider) || providers[0]

  const field = (label: string, key: keyof Settings, type: string, placeholder: string, help?: string) => (
    <div style={{ marginBottom: "1rem" }}>
      <label style={{ display: "block", marginBottom: "0.35rem", color: "#aaa", fontSize: "0.85rem" }}>{label}</label>
      <input type={type} value={String(s[key] ?? "")} onChange={e => update(key, e.target.value)} placeholder={placeholder}
        style={{ width: "100%", padding: "0.75rem", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 8, color: "#fff", fontSize: "0.9rem", outline: "none", boxSizing: "border-box" }} />
      {help && <p style={{ margin: "0.25rem 0 0", fontSize: "0.75rem", color: "#555" }}>{help}</p>}
    </div>
  )

  const section = (title: string, icon: React.ReactNode, children: React.ReactNode) => (
    <section style={{ marginBottom: "2rem", background: "#111", borderRadius: 12, padding: "1.25rem", border: "1px solid #1e1e1e" }}>
      <h2 style={{ fontSize: "0.95rem", fontWeight: 600, marginBottom: "1rem", color: "#6366f1", display: "flex", alignItems: "center", gap: "0.5rem" }}>
        <span>{icon}</span> {title}
      </h2>
      {children}
    </section>
  )

  if (loading) return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Loader2 size={32} style={{ animation: "spin 1s linear infinite" }} />
    </div>
  )

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <header style={{ padding: "1rem 2rem", borderBottom: "1px solid #2a2a2a", display: "flex", alignItems: "center", gap: "1rem" }}>
        <Link href="/" style={{ color: "#888", textDecoration: "none", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.9rem" }}>
          <ArrowLeft size={16} /> Back
        </Link>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 700 }}>Settings</h1>
      </header>
      <div style={{ maxWidth: 680, margin: "0 auto", padding: "2rem" }}>

        {section("LLM Provider", <Zap size={16}/>, <>
          <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1.5rem" }}>
            {providers.map(p => (
              <button key={p.id} type="button" onClick={() => { update("llm_provider", p.id); update("base_url", p.url); update("model", p.model) }}
                style={{ flex: 1, padding: "0.75rem", background: s.llm_provider===p.id?"#6366f1":"#1a1a1a", border:"1px solid "+(s.llm_provider===p.id?"#6366f1":"#2a2a2a"), borderRadius:8, color:"#fff", cursor:"pointer", fontSize:"0.85rem", textAlign:"center" }}>
                <div style={{fontWeight:600}}>{p.label}</div>
                <div style={{fontSize:"0.7rem",color:"#888",marginTop:2}}>{p.hint}</div>
              </button>
            ))}
          </div>
          {field("API Key", "api_key", "password", "sk-...")}
          {field("Base URL", "base_url", "text", "https://api.deepseek.com/v1")}
          {field("Model", "model", "text", "deepseek-chat")}
        </>)}

        {section("ComfyUI", <span style={{fontSize:"1rem"}}>🎨</span>, <>
          {field("Server URL", "comfyui_url", "text", "http://localhost:8188")}
          <div style={{display:"flex",alignItems:"center",gap:"0.75rem",marginTop:"0.5rem"}}>
            <button onClick={checkComfyUI} disabled={checking}
              style={{padding:"0.5rem 1rem",background:checking?"#333":s.comfyui_available?"#22c55e":"#6366f1",color:"#fff",border:"none",borderRadius:6,cursor:"pointer",fontSize:"0.85rem",display:"flex",alignItems:"center",gap:"0.4rem"}}>
              {checking?<><Loader2 size={14} style={{animation:"spin 1s linear infinite"}}/>Checking...</>:s.comfyui_available?<><CheckCircle size={14}/>Connected</>:"Check Connection"}
            </button>
            {s.comfyui_available && <span style={{color:"#22c55e",fontSize:"0.85rem"}}>ComfyUI is running</span>}
            {!s.comfyui_available && <span style={{color:"#555",fontSize:"0.85rem"}}>{s.comfyui_url} not reachable</span>}
          </div>
        </>)}

        {section("Video Generation APIs (optional)", <span style={{fontSize:"1rem"}}>🎬</span>, <>
          {field("Kling API Key", "kling_api_key", "password", "sk-...", "For Kling AI video generation")}
          {field("Kling API URL", "kling_api_url", "text", "https://api.klingai.com/v1")}
          {field("Seedance API Key", "seedance_api_key", "password", "sk-...", "For Google Seedance video generation")}
          {field("Veo API Key", "veo_api_key", "password", "sk-...", "For Google Veo video generation")}
        </>)}

        <button onClick={handleSave} disabled={saving}
          style={{width:"100%",padding:"1rem",background:saving?"#333":saved?"#22c55e":"linear-gradient(135deg,#6366f1,#8b5cf6)",color:"#fff",border:"none",borderRadius:10,fontSize:"1.1rem",fontWeight:600,cursor:saving?"not-allowed":"pointer",display:"flex",alignItems:"center",justifyContent:"center",gap:"0.5rem"}}>
          {saving?<><Loader2 size={18} style={{animation:"spin 1s linear infinite"}}/>Saving...</>:saved?<><CheckCircle size={18}/>Saved!</>:<><Save size={18}/>Save Settings</>}
        </button>
        <p style={{marginTop:"1rem",color:"#444",fontSize:"0.75rem",textAlign:"center"}}>Settings saved to data/settings.json</p>
      </div>
    </div>
  )
}
