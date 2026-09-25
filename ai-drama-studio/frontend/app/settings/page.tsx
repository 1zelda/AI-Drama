"use client"
import { useState, useEffect } from "react"
import Link from "next/link"
import { ArrowLeft, Save, CheckCircle, Loader2, Zap } from "lucide-react"
import TopNav from "@/components/TopNav"

interface Settings {
  auto_cleanup?: boolean
  llm_provider: "openai" | "deepseek" | "zhipu" | "custom"
  api_key: string
  base_url: string
  model: string
  comfyui_url: string
  comfyui_available: boolean
  agnes_api_key: string
  agnes_proxy_url: string
  agnes_video_model: string
  agnes_video_mode: string
  modelscope_token: string
  siliconflow_api_key: string
  gemini_api_key: string
  zhipu_api_key: string
  kling_api_key: string
  kling_api_url: string
  seedance_api_key: string
  veo_api_key: string
  dashscope_api_key?: string
  vision_api_key?: string
  vision_base_url?: string
  vision_model?: string
  public_asset_base_url?: string
  gptsovits_url?: string
}

const AGNES_MODELS = [
  { id: "agnes-video-2.5-flash", label: "Agnes Video 2.5 Flash（快，推荐日常）" },
  { id: "agnes-video-2.5", label: "Agnes Video 2.5（质量优先）" },
  { id: "agnes-video-v2.0", label: "Agnes Video v2.0（旧版）" },
]

export default function SettingsPage() {
  const [s, setS] = useState<Settings>({
    llm_provider: "deepseek",
    api_key: "",
    base_url: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
    comfyui_url: "http://localhost:8188",
    comfyui_available: false,
    agnes_api_key: "",
    agnes_proxy_url: "http://127.0.0.1:57324",
    agnes_video_model: "agnes-video-2.5-flash",
    agnes_video_mode: "T2V",
    modelscope_token: "",
    siliconflow_api_key: "",
    gemini_api_key: "",
    zhipu_api_key: "",
    kling_api_key: "",
    kling_api_url: "https://api.klingai.com/v1",
    seedance_api_key: "",
    veo_api_key: "",
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)
  const [checking, setChecking] = useState(false)
  const [health, setHealth] = useState<any>(null)
  const [healthLoading, setHealthLoading] = useState(false)
  const [cleanupPlan, setCleanupPlan] = useState<any>(null)
  const [cleanupBusy, setCleanupBusy] = useState(false)
  const [cleanupMsg, setCleanupMsg] = useState("")

  /** 删文件不可恢复：先出计划给用户看，确认后才真删。 */
  const runCleanup = async (confirmDelete: boolean) => {
    setCleanupBusy(true)
    setCleanupMsg("")
    try {
      const res = await fetch("/api/system/cleanup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: !confirmDelete }),
      })
      const data = await res.json()
      if (confirmDelete) {
        setCleanupPlan(null)
        setCleanupMsg(`已清理 ${data.deleted} 个文件，释放约 ${data.mb} MB`)
      } else {
        setCleanupPlan(data)
      }
    } catch (e: any) {
      setCleanupMsg("清理失败：" + e.message)
    } finally {
      setCleanupBusy(false)
    }
  }

  useEffect(() => {
    fetch("/api/settings")
      .then(r => r.json())
      .then(data => { setS(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  /** 一键自检：后端真发一次 LLM 请求，并检查生图/生视频/FFmpeg/字幕字体。 */
  const runHealth = async (deep: boolean) => {
    setHealthLoading(true)
    try {
      const res = await fetch(`/api/system/health?deep=${deep}`)
      const data = await res.json()
      setHealth(data.error ? { ok: false, summary: data.error, checks: {} } : data)
    } catch (e: any) {
      setHealth({ ok: false, summary: "自检请求失败：" + e.message, checks: {} })
    } finally {
      setHealthLoading(false)
    }
  }

  const CHECK_LABELS: Record<string, string> = {
    llm: "剧本 LLM",
    image: "生图通道",
    video: "生视频通道",
    tts: "AI 配音",
    ffmpeg: "FFmpeg 合成",
    subtitle_font: "中文字幕字体",
    comfyui: "ComfyUI（可选，无 GPU 可忽略）",
    restyle: "片段魔改线（可选：视觉反推 / 整段编辑）",
  }

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
    { id: "zhipu" as const, label: "智谱 GLM", url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash", hint: "免费 · 一个Key通吃" },
    { id: "deepseek" as const, label: "DeepSeek", url: "https://api.deepseek.com/v1", model: "deepseek-chat", hint: "需余额" },
    { id: "openai" as const, label: "OpenAI", url: "https://api.openai.com/v1", model: "gpt-4o", hint: "GPT-4o" },
    { id: "custom" as const, label: "自定义", url: "", model: "", hint: "任意 OpenAI 兼容" },
  ]
  const cur = providers.find(p => p.id === s.llm_provider) || providers[0]

  const field = (label: string, key: keyof Settings, type: string, placeholder: string, help?: string) => (
    <div style={{ marginBottom: "1rem" }}>
      <label style={{ display: "block", marginBottom: "0.35rem", color: "#aaa", fontSize: "0.85rem" }}>{label}</label>
      <input type={type} value={typeof s[key] === 'boolean' ? '' : String(s[key] ?? '')} onChange={e => update(key, e.target.value)} placeholder={placeholder}
        style={{ width: "100%", padding: "0.75rem", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 8, color: "#fff", fontSize: "0.9rem", outline: "none", boxSizing: "border-box" }} />
      {help && <p style={{ margin: "0.25rem 0 0", fontSize: "0.75rem", color: "#555" }}>{help}</p>}
    </div>
  )

  const healthBtn = (busy: boolean): React.CSSProperties => ({
    flex: "1 1 180px",
    padding: "0.7rem 1rem",
    background: busy ? "#333" : "linear-gradient(135deg,#6366f1,#8b5cf6)",
    color: "#fff",
    border: "none",
    borderRadius: 8,
    fontSize: "0.88rem",
    fontWeight: 600,
    cursor: busy ? "not-allowed" : "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  })

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
      <TopNav title="设置" subtitle="填 Key 后记得点保存，会同步到后端 .env" />
      <div style={{ maxWidth: 680, margin: "0 auto", padding: "2rem 1.25rem 4rem" }}>

        {section("一键自检 · 能不能出片", <Zap size={16}/>, <>
          <p style={{ margin: "0 0 1rem", fontSize: "0.78rem", color: "#777", lineHeight: 1.6 }}>
            点一下就知道「缺哪个 Key、现在能不能出片」。自检会真发一次 LLM 请求确认 Key 有效；
            生图/生视频默认只检查配置，「深度检测」会真生成一张小图验证（消耗极少额度）。
          </p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button onClick={() => runHealth(false)} disabled={healthLoading} style={healthBtn(healthLoading)}>
              {healthLoading ? <><Loader2 size={14} style={{animation:"spin 1s linear infinite"}}/>检测中…</> : "一键自检"}
            </button>
            <button onClick={() => runHealth(true)} disabled={healthLoading} style={{...healthBtn(healthLoading), background:"#1a1a1a", border:"1px solid #2a2a2a"}}>
              深度检测（实测生图）
            </button>
          </div>

          {health && (
            <div style={{ marginTop: "1rem" }}>
              <div style={{
                padding: "0.7rem 0.9rem", borderRadius: 8, fontSize: "0.85rem", lineHeight: 1.6,
                background: health.ok ? "#052e1633" : "#7f1d1d33",
                border: `1px solid ${health.ok ? "#166534" : "#7f1d1d"}`,
                color: health.ok ? "#86efac" : "#fca5a5",
              }}>
                {health.ok ? "✅ " : "⚠️ "}{health.summary}
              </div>
              <div style={{ marginTop: "0.75rem", display: "grid", gap: 6 }}>
                {Object.entries(health.checks || {}).map(([key, val]: any) => (
                  <div key={key} style={{
                    display: "flex", alignItems: "flex-start", gap: 8,
                    padding: "0.5rem 0.7rem", background: "#0d0d0d",
                    border: "1px solid #1e1e1e", borderRadius: 8, fontSize: "0.78rem",
                  }}>
                    <span style={{ color: val.ok ? "#22c55e" : "#ef4444", width: 14 }}>{val.ok ? "●" : "✕"}</span>
                    <span style={{ width: 168, color: "#ccc", flexShrink: 0 }}>{CHECK_LABELS[key] || key}</span>
                    <span style={{ color: "#777", flex: 1, wordBreak: "break-all" }}>{val.detail}</span>
                  </div>
                ))}
              </div>
              {!health.ok && (
                <p style={{ margin: "0.75rem 0 0", fontSize: "0.75rem", color: "#888", lineHeight: 1.7 }}>
                  最便宜的起步组合：<b style={{color:"#a5b4fc"}}>智谱 BigModel</b> 一个 Key 同时搞定
                  生图（cogview-3-flash）和生视频（cogvideox-flash），两个都是官方免费模型。
                  填完点下面的保存，再点一次自检即可。
                </p>
              )}
            </div>
          )}
        </>)}

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
          {s.llm_provider === "zhipu" ? (
            <p style={{ margin: 0, fontSize: "0.78rem", color: "#777", lineHeight: 1.7 }}>
              智谱 GLM-4-Flash 是官方免费模型，不在这里填 Key —— 它复用下面「免费通道」里的
              <b style={{ color: "#a5b4fc" }}> 智谱 API Key</b>。
              一个智谱 Key 同时搞定剧本（GLM-4-Flash）、生图（cogview-3-flash）、生视频（cogvideox-flash）。
            </p>
          ) : (
            <>
              {field("API Key", "api_key", "password", "sk-...")}
              {field("Base URL", "base_url", "text", "https://api.deepseek.com/v1")}
              {field("Model", "model", "text", "deepseek-chat")}
            </>
          )}
        </>)}

        {section("Agnes Video（默认视频引擎）", <span style={{fontSize:"1rem"}}>🎥</span>, <>
          {field("AGNES API Key", "agnes_api_key", "password", "ag-...", "留空则走 AGNES_PROXY_URL 本地代理")}
          {field("本地代理 URL", "agnes_proxy_url", "text", "http://127.0.0.1:57324", "有官方 Key 可清空此字段直连")}
          <div style={{ marginBottom: "1rem" }}>
            <label style={{ display: "block", marginBottom: "0.35rem", color: "#aaa", fontSize: "0.85rem" }}>默认视频模型</label>
            <select value={s.agnes_video_model} onChange={e => update("agnes_video_model", e.target.value)}
              style={{ width: "100%", padding: "0.75rem", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 8, color: "#fff", fontSize: "0.9rem", outline: "none", boxSizing: "border-box" }}>
              {AGNES_MODELS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </div>
          <div style={{ marginBottom: "1rem" }}>
            <label style={{ display: "block", marginBottom: "0.35rem", color: "#aaa", fontSize: "0.85rem" }}>默认生成模式</label>
            <select value={s.agnes_video_mode} onChange={e => update("agnes_video_mode", e.target.value)}
              style={{ width: "100%", padding: "0.75rem", background: "#141414", border: "1px solid #2a2a2a", borderRadius: 8, color: "#fff", fontSize: "0.9rem", outline: "none", boxSizing: "border-box" }}>
              <option value="T2V">T2V 文生视频</option>
              <option value="I2V">I2V 图生视频（首帧）</option>
              <option value="FLF">FLF 首尾帧</option>
              <option value="R2V">R2V 参考图</option>
            </select>
          </div>
        </>)}

        {section("免费生图 / 生视频通道（零成本兜底）", <span style={{fontSize:"1rem"}}>🆓</span>, <>
          <p style={{ margin: "0 0 1rem", fontSize: "0.78rem", color: "#777", lineHeight: 1.6 }}>
            <b style={{ color: "#a5b4fc" }}>最省事的起步：只填一个「智谱 API Key」</b>，
            就能同时用上剧本 GLM-4-Flash、生图 cogview-3-flash、生视频 cogvideox-flash，
            三个都是官方免费模型，不用再注册别家。
            想提质量再加：<b>ModelScope 魔搭</b>（每日 2000 次，可跑 Wan2.2 视频 / Qwen-Image）
            或 <b>SiliconFlow Kolors</b>（免费层生图）。
            配多家的 Key 后，流水线会按「免费优先 + 已配置优先」自动挑通道，不用改 JSON。
          </p>
          {field("ModelScope 魔搭 Token（免费额度）", "modelscope_token", "password", "ms-xxxx", "https://modelscope.cn/my/myaccesstoken 免费申请")}
          {field("SiliconFlow API Key（免费层生图）", "siliconflow_api_key", "password", "sk-...", "https://cloud.siliconflow.cn")}
          {field("Gemini API Key（免费层 Nano Banana）", "gemini_api_key", "password", "AIza...", "https://aistudio.google.com/apikey")}
          {field("智谱 API Key（CogView-Flash 生图）", "zhipu_api_key", "password", "xxxx.yyyy", "https://open.bigmodel.cn")}
        </>)}

        {section("片段魔改线（video-restyle）专用，可选", <span style={{fontSize:"1rem"}}>🧩</span>, <>
          <p style={{ margin: "0 0 1rem", fontSize: "0.78rem", color: "#777", lineHeight: 1.6 }}>
            出厂的逐镜重绘路线用上面配好的生图/生视频通道就能跑，这三项<strong style={{color:"#a5b4fc"}}>都可以不填</strong>。
            只有两种情况需要：<b>①</b> 想让它真的「看」懂原片画面（逐镜反推）要一个多模态模型，
            留空时按 <b>VISION_API_KEY → 智谱 Key → 剧本 Key</b> 顺序找，最后那档很可能是纯文字模型、看了等于没看；
            <b>②</b> 打开 <code>video_edit</code>（整段换风格、运动不变）要阿里百炼 Key，
            并且云端得能取到你本机的源视频 —— 那需要一个公网地址。
          </p>
          {field("视觉模型 API Key（反推原片画面）", "vision_api_key", "password", "留空则用智谱 Key / 剧本 Key", "要能看图：gpt-4o、glm-4v-flash、qwen-vl 一类")}
          {field("视觉模型 Base URL", "vision_base_url", "text", "留空沿用剧本的 OpenAI 兼容地址")}
          {field("视觉模型名", "vision_model", "text", "留空自动选：填了 VISION_API_KEY 用 gpt-4o-mini，用智谱 Key 用 glm-4v-flash")}
          {field("阿里百炼 DashScope Key（整段视频编辑）", "dashscope_api_key", "password", "sk-...", "只给 wan2.7-videoedit 这条路线用；https://bailian.console.aliyun.com")}
          {field("公网素材地址 PUBLIC_ASSET_BASE_URL", "public_asset_base_url", "text", "https://你的域名 或内网穿透后的地址", "把本机 output/ 里的图片、源视频发布成云端能取的 URL；只有走云端且要吃本地图/视频时才需要")}
          {field("GPT-SoVITS 服务地址（克隆音色配音）", "gptsovits_url", "text", "http://127.0.0.1:9880", "配音节点 engine=gptsovits 时才用，edge-tts 不需要")}
        </>)}

        {section("ComfyUI（远程节点）", <span style={{fontSize:"1rem"}}>🎨</span>, <>
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

        {section("产出与清理", <span style={{fontSize:"1rem"}}>🧹</span>, <>
          <p style={{ margin: "0 0 1rem", fontSize: "0.78rem", color: "#777", lineHeight: 1.6 }}>
            跑一次 6 镜头的短剧会产出约 20 个中间文件（分镜图、单镜头视频、配音），
            成片合成完它们就没用了。开启后每次跑完自动清掉；
            成片和成片库里的封面<b style={{color:"#a5b4fc"}}>永远不会被删</b>。
          </p>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.85rem", color: "#ccc", marginBottom: 12 }}>
            <input type="checkbox" checked={!!s.auto_cleanup}
              onChange={e => { setS(prev => ({...prev, auto_cleanup: e.target.checked})); setSaved(false) }} />
            跑完自动清理中间产物
          </label>
          <button onClick={() => void runCleanup(false)} disabled={cleanupBusy || !!cleanupPlan}
            style={{ ...healthBtn(cleanupBusy), flex: "0 0 auto", background: "#1a1a1a", border: "1px solid #2a2a2a" }}>
            {cleanupBusy ? "扫描中…" : "立即清理中间产物"}
          </button>

          {cleanupPlan && (
            <div style={{ marginTop: "0.9rem", padding: "0.7rem 0.9rem", background: "#1c1206",
              border: "1px solid #78350f", borderRadius: 8, fontSize: "0.8rem", color: "#fcd34d", lineHeight: 1.6 }}>
              将删除 <b>{cleanupPlan.files}</b> 个文件，约 <b>{cleanupPlan.mb} MB</b>
              {cleanupPlan.tmp_dirs ? `（另含 ${cleanupPlan.tmp_dirs} 个临时目录）` : ""}
              {cleanupPlan.samples?.length > 0 && (
                <div style={{ color: "#a1a1aa", fontSize: "0.7rem", marginTop: 6 }}>
                  {cleanupPlan.samples.slice(0, 6).join(" · ")}…
                </div>
              )}
              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <button onClick={() => void runCleanup(true)} disabled={cleanupBusy}
                  style={{ ...healthBtn(cleanupBusy), flex: "0 0 auto", background: "#7c2d12" }}>
                  确认删除
                </button>
                <button onClick={() => setCleanupPlan(null)}
                  style={{ ...healthBtn(false), flex: "0 0 auto", background: "#1a1a1a", border: "1px solid #2a2a2a" }}>
                  取消
                </button>
              </div>
            </div>
          )}
          {cleanupMsg && !cleanupPlan && (
            <p style={{ margin: "0.8rem 0 0", fontSize: "0.78rem", color: "#86efac" }}>{cleanupMsg}</p>
          )}
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
