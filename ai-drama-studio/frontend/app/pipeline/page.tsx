"use client";

/**
 * 流水线画布：DAG 编排 + 节点参数编辑 + 一键运行 + SSE 实时进度 + 产物预览。
 *
 * 相比旧版补齐了三件事：
 * 1. 画面控制：适应画面 / 重置布局 / 缩放显示 / 节点状态图例，拖拽后的位置不再被重排冲掉。
 * 2. 镜头路由接进参数面板：填了 shot_type 就能看到会被派发到哪个模型、套哪条运动模板。
 * 3. 产物预览：运行中和结束后都能直接看图/看片，不用再去 output 目录翻文件。
 * 4. AI 助手：右侧「AI 助手」页用一句话改节点参数、提示词模板、增删节点，
 *    后端只出提案，勾选确认后才写盘（见 components/CopilotPanel.tsx）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { API_BASE, api, sseUrl } from "@/lib/api";
import TopNav from "@/components/TopNav";
import CopilotPanel from "@/components/CopilotPanel";
import {
  Play, Square, Save, Maximize2, LayoutGrid, RefreshCw,
  X, Download, ImageIcon, Film, Plus, Trash2,
} from "lucide-react";

// ---------------------------------------------------------------- 类型

type NodeConfig = Record<string, any>;
type WorkflowDoc = { name: string; description?: string; nodes: Record<string, NodeConfig>; [k: string]: any };
type NodeStatus = "idle" | "running" | "done" | "failed" | "waiting";
type RunState = Record<string, { status: NodeStatus; progress?: number }>;
type LogItem = { ts: string; text: string; kind: "info" | "ok" | "err" };
type Artifact = {
  node_id: string; kind: "image" | "video"; url: string;
  name: string; size: number; mtime: number; workflow?: string;
  width?: number; height?: number;
};
type ShotType = { id: string; label: string; preferred: string; fallback: string; banned: string[] };

const TYPE_COLORS: Record<string, string> = {
  llm: "#6366f1", text: "#6366f1", image: "#06b6d4", comfyui: "#06b6d4",
  comfyui_image: "#06b6d4", video: "#f59e0b", comfyui_video: "#f59e0b",
  agnes_video: "#f59e0b", tts: "#a855f7", video_input: "#84cc16", postprocess: "#14b8a6",
  ffmpeg: "#10b981", noop: "#6b7280",
};

// 权威列表是后端的 /api/runs/meta/node-types（它直接抄引擎的派发表）；这里只是
// 后端没起来时的兜底，少了谁就会在「新建节点」下拉里看不见谁。
const FALLBACK_NODE_TYPES = [
  "llm", "text", "image", "comfyui_image", "comfyui",
  "video", "comfyui_video", "agnes_video", "tts",
  "video_input", "ffmpeg", "postprocess", "noop",
];

const STATUS_COLORS: Record<NodeStatus, string> = {
  idle: "#333", running: "#f59e0b", done: "#10b981", failed: "#ef4444", waiting: "#8b5cf6",
};

const STATUS_LABEL: Record<NodeStatus, string> = {
  idle: "待运行", running: "运行中", done: "已完成", failed: "失败", waiting: "待审批",
};

// ---------------------------------------------------------------- 布局

function layout(nodes: Record<string, NodeConfig>, saved: Record<string, { x: number; y: number }>) {
  const ids = Object.keys(nodes);
  const depth: Record<string, number> = {};
  const visiting = new Set<string>();

  const d = (id: string): number => {
    if (depth[id] !== undefined) return depth[id];
    if (visiting.has(id)) return 0; // 循环保护
    visiting.add(id);
    const deps: string[] = nodes[id]?.depends_on || [];
    depth[id] = deps.length ? Math.max(...deps.map(d)) + 1 : 0;
    visiting.delete(id);
    return depth[id];
  };
  ids.forEach(d);

  const byLevel: Record<number, string[]> = {};
  ids.forEach((id) => {
    const lv = depth[id] || 0;
    (byLevel[lv] = byLevel[lv] || []).push(id);
  });

  const flowNodes: Node[] = [];
  for (const lv of Object.keys(byLevel).map(Number).sort((a, b) => a - b)) {
    byLevel[lv].forEach((id, i) => {
      flowNodes.push({
        id,
        type: "drama",
        // 拖过的位置优先，否则按层排布
        position: saved[id] ?? { x: lv * 300, y: i * 130 },
        data: { label: id, cfg: nodes[id] },
      });
    });
  }
  const flowEdges: Edge[] = [];
  ids.forEach((id) =>
    (nodes[id]?.depends_on || []).forEach((dep: string) =>
      flowEdges.push({
        id: `${dep}->${id}`,
        source: dep,
        target: id,
        animated: true,
        style: { stroke: "#4b5563" },
      })
    )
  );
  return { flowNodes, flowEdges };
}

// ---------------------------------------------------------------- 节点

function DramaNode({ data }: NodeProps) {
  const cfg = (data as any).cfg || {};
  const status = ((data as any).status || "idle") as NodeStatus;
  const progress = (data as any).progress;
  const type = (cfg.type || "llm") as string;
  return (
    <div
      style={{
        minWidth: 196,
        background: "#161622",
        border: `2px solid ${STATUS_COLORS[status] || "#333"}`,
        borderRadius: 10,
        padding: "10px 12px",
        color: "#eee",
        boxShadow: status === "running" ? "0 0 16px rgba(245,158,11,.45)" : "none",
        transition: "border-color .2s",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: "#555" }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{
          background: TYPE_COLORS[type] || "#666", color: "#fff", borderRadius: 6,
          padding: "1px 8px", fontSize: 11, fontWeight: 700,
        }}>
          {type}
        </span>
        <strong style={{ fontSize: 14 }}>{String(data.label)}</strong>
      </div>
      <div style={{ fontSize: 11, color: "#999" }}>
        {cfg.shot_type ? `🎯 ${cfg.shot_type} · ` : ""}
        {cfg.provider || cfg.model || "自动路由"}
        {cfg.foreach ? ` · foreach ${cfg.foreach}` : ""}
      </div>
      {status === "running" && (
        <div style={{ marginTop: 6, height: 5, background: "#2a2a3a", borderRadius: 3 }}>
          <div style={{
            width: `${Math.min(100, progress ?? 35)}%`, height: "100%",
            background: "#f59e0b", borderRadius: 3, transition: "width .4s",
          }} />
        </div>
      )}
      {status !== "idle" && status !== "running" && (
        <div style={{ marginTop: 5, fontSize: 10, color: STATUS_COLORS[status] }}>
          ● {STATUS_LABEL[status]}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: "#555" }} />
    </div>
  );
}

const nodeTypes = { drama: DramaNode };

// ---------------------------------------------------------------- 可编辑字段

const FIELDS: { key: string; label: string; type?: "text" | "number" | "textarea"; hint?: string }[] = [
  { key: "type", label: "节点类型", hint: "以引擎分发表为准，见 /api/runs/meta/node-types" },
  { key: "provider", label: "Provider（留空则按镜头类型自动路由）" },
  { key: "model", label: "模型" },
  { key: "shot_type", label: "镜头类型（决定模型派发与运动模板）" },
  { key: "micro_action", label: "微动作（一片段一动词）" },
  { key: "workflow_file", label: "ComfyUI 工作流" },
  { key: "prompt", label: "提示词", type: "textarea" },
  { key: "prompt_template", label: "提示词模板" },
  { key: "negative_prompt", label: "反向提示词", type: "textarea" },
  { key: "seed", label: "Seed", type: "number" },
  { key: "width", label: "宽", type: "number" },
  { key: "height", label: "高", type: "number" },
  { key: "steps", label: "步数", type: "number" },
  { key: "cfg", label: "CFG", type: "number" },
  { key: "seconds", label: "时长(秒)", type: "number" },
  { key: "foreach", label: "Foreach(批量源)" },
  { key: "first_frame_from", label: "首帧来源节点" },
  { key: "output", label: "输出名" },
  { key: "preview_gate", label: "预览门（跑完几项停下等确认，0/留空=不拦）", type: "number" },
  { key: "concurrency", label: "foreach 并发数", type: "number" },
  { key: "retry", label: "重试次数", type: "number" },
  { key: "timeout", label: "节点超时(秒)", type: "number" },
];

// ---------------------------------------------------------------- 主页面

function PipelineInner() {
  const [workflows, setWorkflows] = useState<string[]>([]);
  const [current, setCurrent] = useState("");
  const [doc, setDoc] = useState<WorkflowDoc | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [runState, setRunState] = useState<RunState>({});
  const [log, setLog] = useState<LogItem[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [msg, setMsg] = useState("");
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<"params" | "artifacts" | "log" | "ai">("params");
  const [shotTypes, setShotTypes] = useState<ShotType[]>([]);
  const [engineTypes, setEngineTypes] = useState<string[]>(FALLBACK_NODE_TYPES);
  const [newNodeId, setNewNodeId] = useState("");
  const [newNodeType, setNewNodeType] = useState("image");
  const [routingInfo, setRoutingInfo] = useState<any>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [artifactSource, setArtifactSource] = useState<"run" | "recent" | null>(null);
  const [lightbox, setLightbox] = useState<Artifact | null>(null);
  const [zoom, setZoom] = useState(1);

  const esRef = useRef<EventSource | null>(null);
  const lastSeqRef = useRef(0);
  const pollRef = useRef<any>(null);
  const runningRef = useRef(false);
  const tabRef = useRef(tab);
  useEffect(() => { tabRef.current = tab; }, [tab]);
  const positionsRef = useRef<Record<string, { x: number; y: number }>>({});
  const { fitView, getZoom } = useReactFlow();

  const { flowNodes, flowEdges } = useMemo(
    () => (doc ? layout(doc.nodes, positionsRef.current) : { flowNodes: [], flowEdges: [] }),
    [doc]
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(flowNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(flowEdges);

  const addLog = useCallback((text: string, kind: LogItem["kind"] = "info") =>
    setLog((l) => [{ ts: new Date().toLocaleTimeString(), text, kind }, ...l].slice(0, 300)), []);

  // 首屏：工作流列表 + 镜头类型
  useEffect(() => {
    api<{ workflows: string[] }>("/api/runs/")
      .then((d) => setWorkflows(d.workflows))
      .catch((e) => setMsg(`后端不可达：${e.message}（先启动 FastAPI: uvicorn app.main:app --port 8000）`));
    api<{ shot_types: ShotType[] }>("/api/routing/")
      .then((d) => setShotTypes(d.shot_types || []))
      .catch(() => {});
    api<{ node_types: string[] }>("/api/runs/meta/node-types")
      .then((d) => d.node_types?.length && setEngineTypes(d.node_types))
      .catch(() => {});
    api<{ items: Artifact[] }>("/api/runs/output/recent?limit=40")
      .then((d) => setArtifacts(d.items || []))
      .catch(() => {});
  }, []);

  // 布局变化时同步进画布（保留手工拖过的坐标）
  useEffect(() => {
    setNodes(flowNodes.map((n) => ({
      ...n,
      data: { ...n.data, status: runState[n.id]?.status || "idle", progress: runState[n.id]?.progress },
    })));
    setEdges(flowEdges);
    if (flowNodes.length) setTimeout(() => fitView({ padding: 0.18, duration: 300 }), 60);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowNodes, flowEdges]);

  // 运行状态推给节点
  useEffect(() => {
    setNodes((ns) => ns.map((n) => ({
      ...n,
      data: { ...n.data, status: runState[n.id]?.status || "idle", progress: runState[n.id]?.progress },
    })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runState]);

  // 记住拖动结果，避免重排时被冲掉
  useEffect(() => {
    nodes.forEach((n) => { positionsRef.current[n.id] = n.position; });
  }, [nodes]);

  // 选中节点变化时拉取路由预览
  useEffect(() => {
    const st = doc && selected ? doc.nodes[selected]?.shot_type : null;
    if (!st) { setRoutingInfo(null); return; }
    let alive = true;
    api("/api/routing/preview", { method: "POST", body: JSON.stringify({ shot_type: st }) })
      .then((d) => { if (alive) setRoutingInfo(d); })
      .catch(() => { if (alive) setRoutingInfo(null); });
    return () => { alive = false; };
  }, [doc, selected]);

  const pullArtifacts = useCallback(async (rid: string | null) => {
    try {
      if (rid) {
        const list = (await api<{ artifacts: Artifact[] }>(`/api/runs/${rid}/artifacts`)).artifacts;
        // 本次运行还没落盘时不覆盖，避免把历史产物误当成本次结果
        if (list?.length) { setArtifacts(list); setArtifactSource("run"); }
      } else {
        const list = (await api<{ items: Artifact[] }>("/api/runs/output/recent?limit=40")).items;
        setArtifacts(list || []);
        setArtifactSource("recent");
      }
    } catch { /* 产物拉取失败不打扰主流程 */ }
  }, []);

  const loadWorkflow = useCallback(async (name: string) => {
    if (dirty && !window.confirm("有未保存的修改，切换工作流会丢失。确定继续？")) return;
    setCurrent(name);
    setRunState({}); setLog([]); setRunId(null); setSelected(null); setDirty(false);
    positionsRef.current = {};
    try {
      setDoc(await api<WorkflowDoc>(`/api/runs/workflows/${name}`));
      setMsg("");
    } catch (e: any) {
      setMsg(`加载失败：${e.message}`);
    }
  }, [dirty]);

  // AI 助手写完文件后重新拉取，画布才不会拿着旧数据和磁盘打架
  const reload = useCallback(async () => {
    try {
      const d = await api<{ workflows: string[] }>("/api/runs/");
      setWorkflows(d.workflows || []);
    } catch { /* 列表刷新失败不影响下面的文档重取 */ }
    if (!current) return;
    setDirty(false);
    try {
      setDoc(await api<WorkflowDoc>(`/api/runs/workflows/${current}`));
      addLog("↺ 已重新拉取工作流", "info");
    } catch (e: any) {
      setMsg(`重新加载失败：${e.message}`);
    }
  }, [current, addLog]);

  // 手工增删节点：只改内存里的 doc，点「保存」才落盘（后端会做同一套校验）
  const addNode = () => {
    if (!doc) return;
    const id = newNodeId.trim();
    if (!id) { setMsg("先给新节点起个 id"); return; }
    if (doc.nodes[id]) { setMsg(`节点 ${id} 已存在`); return; }
    setDoc({
      ...doc,
      nodes: {
        ...doc.nodes,
        [id]: { type: newNodeType, depends_on: selected ? [selected] : [], prompt: "" },
      },
    });
    setNewNodeId("");
    setSelected(id);
    setDirty(true);
    setMsg("");
    addLog(`＋ 已加入节点 ${id}（${newNodeType}），保存后生效`, "info");
  };

  const removeNode = () => {
    if (!doc || !selected) return;
    if (!window.confirm(`删除节点 ${selected}？下游对它的依赖会自动去掉。`)) return;
    const nodes: Record<string, NodeConfig> = {};
    for (const [id, cfg] of Object.entries(doc.nodes)) {
      if (id === selected) continue;
      nodes[id] = { ...cfg, depends_on: ((cfg.depends_on || []) as string[]).filter((d: string) => d !== selected) };
    }
    setDoc({ ...doc, nodes });
    addLog(`－ 已移除节点 ${selected}，保存后生效`, "info");
    setSelected(null);
    setDirty(true);
  };

  const createWorkflow = async () => {
    const name = window.prompt("新工作流的名字（会存成 config/workflows/<名字>.json）");
    if (!name?.trim()) return;
    try {
      await api(`/api/runs/workflows/${encodeURIComponent(name.trim())}`, { method: "POST" });
      setWorkflows((w) => [...new Set([...w, name.trim()])].sort());
      setDirty(false);
      await loadWorkflow(name.trim());
      setMsg("");
    } catch (e: any) {
      setMsg(`新建失败：${e.message}`);
    }
  };

  const deleteWorkflow = async () => {
    if (!current) return;
    if (!window.confirm(`删除工作流 ${current}？该文件会从 config/workflows 移除。`)) return;
    try {
      await api(`/api/runs/workflows/${current}`, { method: "DELETE" });
      setWorkflows((w) => w.filter((x) => x !== current));
      setDoc(null); setCurrent(""); setSelected(null); setDirty(false);
      setMsg("已删除");
    } catch (e: any) {
      setMsg(`删除失败：${e.message}`);
    }
  };

  // ------------------------------------------------------------ 运行

  const attachSSE = useCallback((rid: string) => {
    esRef.current?.close();
    const es = new EventSource(sseUrl(rid, lastSeqRef.current));
    esRef.current = es;

    const handle = (e: any) => {
      if (typeof e.seq === "number") lastSeqRef.current = Math.max(lastSeqRef.current, e.seq);
      if (e.type === "node_start") {
        setRunState((s) => ({ ...s, [e.node_id]: { status: "running" } }));
        addLog(`▶ ${e.node_id} 开始`, "info");
      } else if (e.type === "node_progress") {
        setRunState((s) => ({ ...s, [e.node_id]: { status: "running", progress: e.value } }));
      } else if (e.type === "item_done") {
        setRunState((s) => ({ ...s, [e.node_id]: { status: "running", progress: e.progress } }));
      } else if (e.type === "node_done") {
        setRunState((s) => ({ ...s, [e.node_id]: { status: "done", progress: 100 } }));
        addLog(`✔ ${e.node_id} 完成`, "ok");
        pullArtifacts(rid);
      } else if (e.type === "node_error" || e.type === "node_failed") {
        setRunState((s) => ({ ...s, [e.node_id]: { status: "failed" } }));
        addLog(`✘ ${e.node_id} 失败：${e.error || ""}`, "err");
      } else if (e.type === "node_retry") {
        addLog(`↻ ${e.node_id} 第 ${e.attempt} 次重试：${e.error || ""}`, "info");
      } else if (e.type === "node_waiting_approval") {
        setRunState((s) => ({ ...s, [e.node_id]: { status: "waiting" } }));
        addLog(`⏸ ${e.node_id} 等待审批`, "info");
      } else if (e.type === "run_end") {
        setRunning(false);
        es.close();
        pullArtifacts(rid);
        if (e.status === "completed") {
          addLog("✔ 流水线完成", "ok");
          // 只在用户正盯着日志时自动切到产物，别把正在调参的人拽走
          if (tabRef.current === "log") setTab("artifacts");
        } else if (e.status === "cancelled") addLog("⏹ 已取消", "info");
        else addLog(`✘ 流水线结束：${e.status} ${e.error || ""}`, "err");
      }
    };

    es.onmessage = (ev) => {
      try { handle(JSON.parse(ev.data)); } catch { /* 忽略坏帧 */ }
    };
    es.onerror = () => {
      // 服务端在 run_end 后主动断开，属正常；否则等 1.5s 重连并回放
      if (esRef.current !== es) return;
      es.close();
      setTimeout(() => {
        if (!runningRef.current) return;
        setRunning(true);
        attachSSE(rid);
      }, 1500);
    };
  }, [addLog, pullArtifacts]);

  useEffect(() => { runningRef.current = running; }, [running]);

  const run = async () => {
    if (!doc) return;
    try {
      setMsg("");
      lastSeqRef.current = 0;
      const r = await api<{ run_id: string }>("/api/runs/", {
        method: "POST",
        body: JSON.stringify({ workflow: current, input: {} }),
      });
      setRunId(r.run_id);
      setRunning(true);
      setRunState({});
      setTab("log");
      addLog(`▶ run ${r.run_id} 已启动（${current}）`, "ok");
      // 运行期间定时兜底拉产物，避免只看事件漏掉文件
      pollRef.current = setInterval(() => pullArtifacts(r.run_id), 8000);
      attachSSE(r.run_id);
    } catch (e: any) {
      setMsg(`启动失败：${e.message}`);
    }
  };

  const stopRun = async () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    esRef.current?.close();
    setRunning(false);
    if (!runId) return;
    try {
      await api(`/api/runs/${runId}/cancel`, { method: "POST" });
      addLog("⏹ 已请求取消", "info");
    } catch (e: any) {
      addLog(`取消失败：${e.message}`, "err");
    }
    pullArtifacts(runId);
  };

  useEffect(() => () => {
    esRef.current?.close();
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  const save = async () => {
    if (!doc) return;
    try {
      await api(`/api/runs/workflows/${current}`, { method: "PUT", body: JSON.stringify(doc) });
      addLog("✔ 已保存工作流", "ok");
      setDirty(false);
      setMsg("");
    } catch (e: any) {
      setMsg(`保存失败：${e.message}`);
    }
  };

  const patchSelected = (key: string, value: any) => {
    if (!doc || !selected) return;
    setDirty(true);
    setDoc({ ...doc, nodes: { ...doc.nodes, [selected]: { ...doc.nodes[selected], [key]: value } } });
  };

  const resetLayout = () => {
    positionsRef.current = {};
    setDoc((d) => (d ? { ...d } : d)); // 触发重新布局
    setTimeout(() => fitView({ padding: 0.18, duration: 300 }), 80);
  };

  const selectedCfg = doc && selected ? doc.nodes[selected] : null;
  const failedCount = Object.values(runState).filter((s) => s.status === "failed").length;

  return (
    <main style={{ height: "100vh", display: "flex", flexDirection: "column", background: "#0a0a0a" }}>
      <TopNav
        title="流水线画布"
        subtitle={current || undefined}
        actions={
          <>
            {dirty && (
              <span style={{ fontSize: "0.72rem", color: "#f59e0b", padding: "0.2rem 0.5rem", background: "#f59e0b1a", borderRadius: 6 }}>
                ● 未保存
              </span>
            )}
            <button onClick={save} disabled={!doc} style={{ ...tbBtn, background: dirty ? "#6366f1" : "#171717" }}>
              <Save size={13} /> 保存
            </button>
          </>
        }
      />

      {/* 工具条 */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8, padding: "0.5rem 1.25rem",
        borderBottom: "1px solid #232323", flexWrap: "wrap",
      }}>
        <select
          value={current}
          onChange={(e) => loadWorkflow(e.target.value)}
          style={{
            background: "#161622", color: "#eee", border: "1px solid #333",
            borderRadius: 8, padding: "0.35rem 0.6rem", fontSize: "0.8rem",
          }}
        >
          <option value="">选择工作流…</option>
          {workflows.map((w) => <option key={w} value={w}>{w}</option>)}
        </select>

        <button onClick={createWorkflow} title="新建一条空白工作流" style={tbBtn}>
          <Plus size={13} /> 新建
        </button>
        <button onClick={deleteWorkflow} disabled={!current} title="删除当前工作流文件"
                style={{ ...tbBtn, opacity: current ? 1 : 0.45 }}>
          <Trash2 size={13} /> 删除工作流
        </button>

        {!running ? (
          <button onClick={run} disabled={!doc} style={{ ...tbBtn, background: doc ? "#10b981" : "#222", color: doc ? "#04231a" : "#666" }}>
            <Play size={13} /> 运行
          </button>
        ) : (
          <button onClick={stopRun} style={{ ...tbBtn, background: "#ef4444", color: "#fff" }}>
            <Square size={13} /> 停止
          </button>
        )}

        <button onClick={() => fitView({ padding: 0.18, duration: 300 })} title="适应画面" style={tbBtn}>
          <Maximize2 size={13} /> 适应画面
        </button>
        <button onClick={resetLayout} disabled={!doc} title="按依赖重新排布" style={tbBtn}>
          <LayoutGrid size={13} /> 整理布局
        </button>
        <button onClick={() => pullArtifacts(runId)} title="刷新产物" style={tbBtn}>
          <RefreshCw size={13} /> 刷新产物
        </button>

        <span style={{ fontSize: "0.72rem", color: "#666" }}>
          缩放 {Math.round(zoom * 100)}%
        </span>

        {runId && <span style={{ fontSize: "0.72rem", color: "#666" }}>run: {runId}</span>}
        {failedCount > 0 && (
          <span style={{ fontSize: "0.72rem", color: "#ef4444" }}>{failedCount} 个节点失败</span>
        )}
        {msg && <span style={{ fontSize: "0.75rem", color: "#f59e0b", marginLeft: "auto" }}>{msg}</span>}
      </div>

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* 画布 */}
        <div style={{ flex: 1, position: "relative" }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            onNodeClick={(_, n) => { setSelected(n.id); setTab("params"); }}
            onMove={(_, vp) => setZoom(vp.zoom)}
            onInit={() => setZoom(getZoom())}
            fitView
            minZoom={0.2}
            maxZoom={2.5}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#222" gap={18} />
            <MiniMap
              nodeColor={(n) => TYPE_COLORS[(n.data as any)?.cfg?.type] || "#555"}
              style={{ background: "#111" }}
              maskColor="rgba(0,0,0,.6)"
            />
            <Controls />
          </ReactFlow>

          {/* 状态图例 */}
          <div style={{
            position: "absolute", left: 12, bottom: 12, display: "flex", gap: 10,
            padding: "0.4rem 0.7rem", background: "#111111e6", border: "1px solid #232323",
            borderRadius: 8, fontSize: "0.7rem", color: "#888", zIndex: 5,
          }}>
            {(Object.keys(STATUS_LABEL) as NodeStatus[]).map((s) => (
              <span key={s} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <i style={{ width: 8, height: 8, borderRadius: 999, background: STATUS_COLORS[s] }} />
                {STATUS_LABEL[s]}
              </span>
            ))}
          </div>

          {!doc && (
            <div style={{
              position: "absolute", inset: 0, display: "grid", placeItems: "center",
              color: "#555", fontSize: "0.9rem", pointerEvents: "none",
            }}>
              先在左上角选一个工作流
            </div>
          )}
        </div>

        {/* 右侧面板 */}
        <aside style={{
          width: tab === "ai" ? 460 : 380, transition: "width .18s",
          borderLeft: "1px solid #232323", display: "flex", flexDirection: "column", minHeight: 0,
        }}>
          <div style={{ display: "flex", borderBottom: "1px solid #232323" }}>
            {([
              ["params", `节点参数${selected ? ` · ${selected}` : ""}`],
              ["ai", "AI 助手"],
              ["artifacts", `产物 (${artifacts.length})`],
              ["log", "运行日志"],
            ] as const).map(([id, label]) => (
              <button key={id} onClick={() => setTab(id)} style={{
                flex: 1, padding: "0.55rem 0.4rem", background: "none", border: "none",
                borderBottom: tab === id ? "2px solid #6366f1" : "2px solid transparent",
                color: tab === id ? "#c7d2fe" : "#7a7a7a", cursor: "pointer", fontSize: "0.78rem",
              }}>
                {label}
              </button>
            ))}
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: "0.9rem" }}>
            {tab === "params" && (
              selectedCfg ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                  {/* 镜头路由结果 */}
                  {routingInfo && (
                    <div style={{
                      padding: "0.6rem 0.75rem", background: "#101018",
                      border: "1px solid #2a2a4a", borderRadius: 9, fontSize: "0.74rem", color: "#a5b4fc", lineHeight: 1.7,
                    }}>
                      <div>🧭 路由：{routingInfo.shot_type?.label} → <b>{routingInfo.provider}</b>（备选 {routingInfo.fallback || "无"}）</div>
                      {routingInfo.banned?.length > 0 && (
                        <div style={{ color: "#f59e0b" }}>⛔ 禁用：{routingInfo.banned.join("、")}</div>
                      )}
                      {routingInfo.motion_template && (
                        <div style={{ color: "#8b8b8b" }}>🎬 {routingInfo.motion_template}</div>
                      )}
                    </div>
                  )}

                  {FIELDS.map((f) => {
                    const value = selectedCfg[f.key] ?? "";
                    const control =
                      f.key === "type" ? (
                        <select
                          value={value || "llm"}
                          onChange={(e) => patchSelected(f.key, e.target.value)}
                          style={inp}
                        >
                          {engineTypes.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                      ) : f.key === "shot_type" ? (
                        <select
                          value={value || ""}
                          onChange={(e) => patchSelected(f.key, e.target.value || undefined)}
                          style={inp}
                        >
                          <option value="">（不指定）</option>
                          {shotTypes.map((s) => (
                            <option key={s.id} value={s.id}>{s.label}（{s.id}）</option>
                          ))}
                        </select>
                      ) : f.type === "textarea" ? (
                        <textarea
                          value={value}
                          onChange={(e) => patchSelected(f.key, e.target.value)}
                          rows={3}
                          style={{ ...inp, resize: "vertical", lineHeight: 1.6 }}
                        />
                      ) : (
                        <input
                          type={f.type === "number" ? "number" : "text"}
                          value={value}
                          onChange={(e) => patchSelected(f.key,
                            f.type === "number" ? (e.target.value === "" ? undefined : Number(e.target.value)) : e.target.value)}
                          style={inp}
                        />
                      );
                    return (
                      <label key={f.key} style={{ fontSize: "0.74rem", color: "#9ca3af", display: "block" }}>
                        {f.label}
                        {control}
                      </label>
                    );
                  })}

                  <div style={{ fontSize: "0.74rem", color: "#9ca3af" }}>
                    依赖
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
                      {Object.keys(doc?.nodes || {}).filter((id) => id !== selected).map((id) => (
                        <label key={id} style={{
                          display: "inline-flex", alignItems: "center", gap: 4,
                          padding: "0.15rem 0.45rem", background: "#171717",
                          border: "1px solid #2a2a2a", borderRadius: 6, color: "#ccc", fontSize: "0.72rem",
                        }}>
                          <input
                            type="checkbox"
                            checked={(selectedCfg.depends_on || []).includes(id)}
                            onChange={(e) => {
                              const deps = new Set(selectedCfg.depends_on || []);
                              e.target.checked ? deps.add(id) : deps.delete(id);
                              patchSelected("depends_on", [...deps]);
                            }}
                          />
                          {id}
                        </label>
                      ))}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                    <input
                      value={newNodeId}
                      onChange={(e) => setNewNodeId(e.target.value)}
                      placeholder="新节点 id，如 lip_sync"
                      style={inp}
                    />
                    <select value={newNodeType} onChange={(e) => setNewNodeType(e.target.value)}
                            style={{ ...inp, width: 118, marginTop: 4 }}>
                      {engineTypes.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                    <button onClick={addNode} style={{ ...tbBtn, background: "#171717", marginTop: 4, whiteSpace: "nowrap" }}>
                      <Plus size={13} /> 加节点
                    </button>
                  </div>
                  <button onClick={removeNode} style={{ ...tbBtn, background: "#3f1d1d", color: "#fecaca" }}>
                    <Trash2 size={13} /> 删除当前节点
                  </button>
                  <p style={{ color: "#555", fontSize: "0.68rem", lineHeight: 1.6, margin: 0 }}>
                    增删与改动都要点顶部「保存」才写盘；保存前后端会做一次结构校验。
                  </p>
                </div>
              ) : (
                <p style={{ color: "#666", fontSize: "0.8rem", lineHeight: 1.7 }}>
                  点击左侧任意节点即可编辑它的模型、提示词、尺寸、镜头类型等参数；
                  不想手填就切到「AI 助手」，一句话说要改什么。
                </p>
              )
            )}

            {tab === "ai" && (
              <CopilotPanel workflow={current} nodeId={selected} dirty={dirty} onApplied={reload} />
            )}

            {tab === "artifacts" && (
              artifacts.length === 0 ? (
                <p style={{ color: "#666", fontSize: "0.8rem", lineHeight: 1.7 }}>
                  还没有产物。运行流水线后，生成的图片和视频会出现在这里。
                </p>
              ) : (
                <>
                {artifactSource === "recent" && (
                  <p style={{
                    color: "#8b8b8b", fontSize: "0.72rem", lineHeight: 1.7, margin: "0 0 0.6rem",
                    padding: "0.4rem 0.6rem", background: "#121212",
                    border: "1px solid #232323", borderRadius: 8,
                  }}>
                    以下是 output/ 目录里最近的历史产物；运行流水线后会优先显示本次产物。
                  </p>
                )}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.6rem" }}>
                  {artifacts.map((a, i) => (
                    <button
                      key={`${a.url}-${i}`}
                      onClick={() => setLightbox(a)}
                      title={a.name}
                      style={{
                        padding: 0, background: "#121212", border: "1px solid #232323",
                        borderRadius: 9, overflow: "hidden", cursor: "pointer", color: "#fff",
                      }}
                    >
                      {/* contain 完整显示不裁边；容器按产物自身比例，竖屏分镜图才不会被压成小图 */}
                      <div style={{
                        aspectRatio: a.width && a.height ? `${a.width}/${a.height}` : "16 / 9",
                        background: "#05050a",
                        display: "grid", placeItems: "center", overflow: "hidden",
                      }}>
                        {a.kind === "image" ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={`${API_BASE}${a.url}`} alt={a.name}
                               style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} />
                        ) : (
                          <video src={`${API_BASE}${a.url}`} muted preload="metadata"
                                 style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} />
                        )}
                      </div>
                      <div style={{ padding: "0.35rem 0.5rem", textAlign: "left" }}>
                        <div style={{
                          fontSize: "0.68rem", color: "#ccc", overflow: "hidden",
                          textOverflow: "ellipsis", whiteSpace: "nowrap",
                        }}>
                          {a.kind === "video" ? "🎬" : "🖼️"} {a.name}
                        </div>
                        {a.node_id && <div style={{ fontSize: "0.62rem", color: "#666" }}>{a.node_id}</div>}
                      </div>
                    </button>
                  ))}
                </div>
                </>
              )
            )}

            {tab === "log" && (
              log.length === 0 ? (
                <p style={{ color: "#666", fontSize: "0.8rem" }}>暂无日志</p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {log.map((l, i) => (
                    <div key={i} style={{
                      fontSize: "0.74rem", lineHeight: 1.6,
                      color: l.kind === "err" ? "#ef4444" : l.kind === "ok" ? "#10b981" : "#8b8b8b",
                    }}>
                      <span style={{ color: "#555" }}>{l.ts}</span> {l.text}
                    </div>
                  ))}
                </div>
              )
            )}
          </div>
        </aside>
      </div>

      {/* 产物放大预览 */}
      {lightbox && (
        <div
          onClick={() => setLightbox(null)}
          style={{
            position: "fixed", inset: 0, background: "#000000dd", zIndex: 50,
            display: "grid", placeItems: "center", padding: "2rem",
          }}
        >
          <div onClick={(e) => e.stopPropagation()} style={{ maxWidth: "90vw", maxHeight: "90vh", textAlign: "center" }}>
            {lightbox.kind === "image" ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={`${API_BASE}${lightbox.url}`} alt={lightbox.name}
                   style={{ maxWidth: "90vw", maxHeight: "78vh", borderRadius: 10 }} />
            ) : (
              <video src={`${API_BASE}${lightbox.url}`} controls autoPlay
                     style={{ maxWidth: "90vw", maxHeight: "78vh", borderRadius: 10 }} />
            )}
            <div style={{ marginTop: "0.8rem", display: "flex", gap: 8, justifyContent: "center", alignItems: "center" }}>
              <span style={{ color: "#bbb", fontSize: "0.8rem" }}>
                {lightbox.kind === "video" ? <Film size={13} /> : <ImageIcon size={13} />} {lightbox.name}
              </span>
              <a href={`${API_BASE}${lightbox.url}`} download target="_blank" rel="noreferrer"
                 style={{
                   display: "inline-flex", alignItems: "center", gap: 5, padding: "0.35rem 0.8rem",
                   background: "#6366f1", color: "#fff", borderRadius: 8, textDecoration: "none", fontSize: "0.78rem",
                 }}>
                <Download size={13} /> 下载
              </a>
              <button onClick={() => setLightbox(null)} style={{
                display: "inline-flex", alignItems: "center", gap: 5, padding: "0.35rem 0.8rem",
                background: "#171717", color: "#ddd", border: "1px solid #2a2a2a",
                borderRadius: 8, cursor: "pointer", fontSize: "0.78rem",
              }}>
                <X size={13} /> 关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

export default function PipelinePage() {
  return (
    <ReactFlowProvider>
      <PipelineInner />
    </ReactFlowProvider>
  );
}

// ---------------------------------------------------------------- 样式

const tbBtn: React.CSSProperties = {
  display: "inline-flex", alignItems: "center", gap: 5,
  padding: "0.35rem 0.7rem", background: "#171717", color: "#ddd",
  border: "1px solid #2a2a2a", borderRadius: 8, cursor: "pointer", fontSize: "0.76rem",
};

const inp: React.CSSProperties = {
  width: "100%", marginTop: 3, background: "#111", color: "#eee",
  border: "1px solid #2f2f2f", borderRadius: 6, padding: "0.35rem 0.5rem",
  fontSize: "0.76rem", boxSizing: "border-box",
};
