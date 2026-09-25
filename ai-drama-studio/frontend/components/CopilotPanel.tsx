"use client";

/**
 * AI 助手面板：一句话改流水线步骤与提示词。
 *
 * 后端只产出「提案」，这里逐条勾选后确认才会真正写文件（POST /api/copilot/apply）。
 * 应用成功回调 onApplied 让父组件重新拉取工作流，避免画布上的旧数据和磁盘打架。
 */
import { useState } from "react";
import { api } from "@/lib/api";
import { Loader2, Send, AlertTriangle, Check, X } from "lucide-react";

export type Proposal = {
  action: string;
  workflow?: string;
  node_id?: string;
  key?: string;
  base_hash?: string;
  patch?: Record<string, any>;
  node?: Record<string, any>;
  doc?: Record<string, any>;
  reason?: string;
  before?: Record<string, any>;
  also_rewire?: string[];
};

type Turn = { role: "user" | "assistant"; content: string };

const QUICK_ASKS = [
  "把整条链路改成 16:9 横屏",
  "在生图之后加一个配音节点",
  "分镜提示词太干，改得更抓人一些",
  "这条流程哪里最烧钱？怎么省",
];

function fmt(v: any): string {
  if (v === undefined) return "—";
  if (v === null) return "（删除）";
  if (typeof v === "object") return JSON.stringify(v, null, 0);
  const s = String(v);
  return s.length > 120 ? s.slice(0, 117) + "…" : s;
}

/** 一条提案渲染成人类可读的变更清单 */
function describe(p: Proposal): { title: string; lines: string[] } {
  switch (p.action) {
    case "workflow.node.patch":
      return {
        title: `改节点 ${p.workflow}/${p.node_id}`,
        lines: Object.entries(p.patch || {}).map(([k, v]) => `${k}：${fmt(p.before?.[k])} → ${fmt(v)}`),
      };
    case "workflow.node.add": {
      const n = p.node || {};
      return {
        title: `新增节点 ${n.id}（${n.type}）`,
        lines: Object.entries(n)
          .filter(([k]) => k !== "id")
          .map(([k, v]) => `${k}：${fmt(v)}`),
      };
    }
    case "workflow.node.remove":
      return {
        title: `删除节点 ${p.workflow}/${p.node_id}`,
        lines: p.also_rewire?.length ? [`下游 ${p.also_rewire.join("、")} 自动去掉这条依赖`] : [],
      };
    case "workflow.meta.patch":
      return {
        title: `改工作流信息 ${p.workflow}`,
        lines: Object.entries(p.patch || {}).map(([k, v]) => `${k}：${fmt(v)}`),
      };
    case "workflow.create":
      return {
        title: `新建工作流 ${p.workflow}`,
        lines: [`节点：${Object.keys(p.doc?.nodes || {}).join(" → ")}`],
      };
    case "prompt.patch": {
      const t = p.patch?.prompt;
      const lines = Object.entries(p.patch || {})
        .filter(([k]) => k !== "prompt")
        .map(([k, v]) => `${k}：${fmt(p.before?.[k])} → ${fmt(v)}`);
      if (typeof t === "string") {
        lines.push("正文：", `改前 ${fmt(p.before?.prompt)}`, `改后 ${fmt(t)}`);
      }
      return { title: `改提示词模板 ${p.key}`, lines };
    }
    case "prompt.create":
      return {
        title: `新建提示词模板 ${p.key}`,
        lines: ["正文：", fmt(p.doc?.prompt)],
      };
    default:
      return { title: p.action, lines: [fmt(p)] };
  }
}

export default function CopilotPanel({
  workflow, nodeId, dirty, onApplied,
}: {
  workflow: string;
  nodeId?: string | null;
  dirty?: boolean;
  onApplied?: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [rejected, setRejected] = useState<{ raw: any; reason: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string>("");
  const [err, setErr] = useState("");

  const send = async (text?: string) => {
    const msg = (text ?? input).trim();
    if (!msg || busy) return;
    if (!workflow) {
      setErr("先在上方选一个工作流，助手才知道要改哪条链路。");
      return;
    }
    setInput("");
    setErr("");
    setResult("");
    setProposals([]);
    setRejected([]);
    setBusy(true);
    setTurns((t) => [...t, { role: "user", content: msg }]);
    try {
      const history = turns.slice(-8).map((t) => ({ role: t.role, content: t.content }));
      const res = await api<{ reply: string; proposals: Proposal[]; rejected: any[] }>("/api/copilot/chat", {
        method: "POST",
        body: JSON.stringify({ message: msg, workflow, node_id: nodeId || "", history }),
      });
      setTurns((t) => [...t, { role: "assistant", content: res.reply || "（没有可应用的改动）" }]);
      setProposals(res.proposals || []);
      setPicked(new Set((res.proposals || []).map((_, i) => i)));
      setRejected(res.rejected || []);
      if (!(res.proposals || []).length) setResult("这次没有产生可落地的改动，只有说明。");
    } catch (e: any) {
      setErr(`助手出错了：${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    const chosen = proposals.filter((_, i) => picked.has(i));
    if (!chosen.length) {
      setErr("至少勾选一条提案。");
      return;
    }
    if (dirty && !window.confirm("画布上有未保存的修改，应用提案会覆盖它们。仍要继续？")) return;
    setBusy(true);
    setErr("");
    try {
      const res = await api<{ applied: any[]; failed: { reason: string }[]; ok: boolean }>(
        "/api/copilot/apply",
        { method: "POST", body: JSON.stringify({ proposals: chosen }) }
      );
      setResult(
        `已写入 ${res.applied.length} 条` +
          (res.failed.length ? `，被拒 ${res.failed.length} 条：${res.failed.map((f) => f.reason).join("；")}` : "")
      );
      setProposals([]);
      setPicked(new Set());
      if (res.applied.length) onApplied?.();
    } catch (e: any) {
      setErr(`应用失败：${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const drop = () => {
    setProposals([]);
    setPicked(new Set());
    setResult("已放弃这批提案，磁盘上的工作流没动。");
  };

  const toggle = (i: number) =>
    setPicked((s) => {
      const next = new Set(s);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: "100%" }}>
      <p style={{ margin: 0, fontSize: "0.72rem", color: "#7a7a7a", lineHeight: 1.7 }}>
        说人话就能改步骤。助手只会给出<b style={{ color: "#a5b4fc" }}>提案</b>，
        你勾选确认后才写回工作流 / 提示词文件。当前聚焦：
        <b style={{ color: "#c7d2fe" }}> {workflow || "未选工作流"}</b>
        {nodeId ? ` / ${nodeId}` : ""}
      </p>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
        {QUICK_ASKS.map((q) => (
          <button key={q} onClick={() => send(q)} disabled={busy} style={chip}>
            {q}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {turns.map((t, i) => (
          <div key={i} style={t.role === "user" ? userBubble : aiBubble}>
            <span style={{ color: t.role === "user" ? "#818cf8" : "#4ade80", fontSize: "0.66rem" }}>
              {t.role === "user" ? "你" : "AI 助手"}
            </span>
            <div style={{ whiteSpace: "pre-wrap", marginTop: 3 }}>{t.content}</div>
          </div>
        ))}
        {busy && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, color: "#8b8b8b", fontSize: "0.75rem" }}>
            <Loader2 size={13} className="animate-spin" /> 正在读工作流并生成改动…
          </div>
        )}
      </div>

      {proposals.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <div style={{ fontSize: "0.7rem", color: "#9ca3af" }}>
            待确认改动（{picked.size}/{proposals.length}）
          </div>
          {proposals.map((p, i) => {
            const d = describe(p);
            const on = picked.has(i);
            return (
              <div key={i} style={{ ...card, borderColor: on ? "#3f3f6b" : "#232323", opacity: on ? 1 : 0.55 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 7, cursor: "pointer" }}>
                  <input type="checkbox" checked={on} onChange={() => toggle(i)} />
                  <span style={{ fontSize: "0.76rem", color: "#e5e7eb", fontWeight: 600 }}>{d.title}</span>
                </label>
                {p.reason && <div style={note}>为什么：{p.reason}</div>}
                {d.lines.length > 0 && (
                  <pre style={pre}>{d.lines.join("\n")}</pre>
                )}
                {p.also_rewire?.length ? (
                  <div style={{ ...note, color: "#f59e0b" }}>⚠ 会顺带改到：{p.also_rewire.join("、")}</div>
                ) : null}
              </div>
            );
          })}
          <div style={{ display: "flex", gap: 7 }}>
            <button onClick={apply} disabled={busy || !picked.size} style={{ ...btn, background: "#6366f1", color: "#fff" }}>
              <Check size={13} /> 应用所选
            </button>
            <button onClick={drop} disabled={busy} style={{ ...btn, background: "#171717", color: "#ccc" }}>
              <X size={13} /> 全部放弃
            </button>
          </div>
        </div>
      )}

      {rejected.length > 0 && (
        <div style={{ ...card, borderColor: "#7c2d12" }}>
          <div style={{ fontSize: "0.72rem", color: "#fbbf24" }}>
            {rejected.length} 条提案没通过校验，已丢弃：
          </div>
          {rejected.map((r, i) => (
            <div key={i} style={{ ...note, color: "#fca5a5" }}>
              {fmt(r.raw).slice(0, 90)} —— {r.reason}
            </div>
          ))}
        </div>
      )}

      {err && (
        <div style={{ display: "flex", gap: 6, alignItems: "flex-start", color: "#fca5a5", fontSize: "0.73rem" }}>
          <AlertTriangle size={13} style={{ marginTop: 2 }} /> {err}
        </div>
      )}
      {result && <div style={{ fontSize: "0.73rem", color: "#4ade80" }}>{result}</div>}

      <div style={{ display: "flex", gap: 6, marginTop: "auto", paddingTop: 6 }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          rows={2}
          placeholder="例如：把生图那一步换成 16:9，提示词里加上「赛璐璐描边」"
          style={{ ...field, resize: "vertical", lineHeight: 1.55 }}
        />
        <button onClick={() => send()} disabled={busy || !input.trim()} style={{ ...btn, alignSelf: "flex-end" }}>
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
        </button>
      </div>
    </div>
  );
}

const chip: React.CSSProperties = {
  padding: "0.2rem 0.5rem", background: "#14141f", color: "#9ca3af",
  border: "1px solid #26263a", borderRadius: 999, cursor: "pointer", fontSize: "0.68rem",
};

const card: React.CSSProperties = {
  background: "#101018", border: "1px solid #2a2a4a", borderRadius: 9,
  padding: "0.5rem 0.6rem",
};

const note: React.CSSProperties = { fontSize: "0.69rem", color: "#8b8b8b", marginTop: 4, lineHeight: 1.6 };

const pre: React.CSSProperties = {
  margin: "5px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-all",
  fontSize: "0.68rem", lineHeight: 1.6, color: "#a5b4fc",
  background: "#0a0a12", borderRadius: 6, padding: "0.35rem 0.5rem",
  maxHeight: 190, overflowY: "auto",
};

const userBubble: React.CSSProperties = {
  background: "#15151f", borderRadius: 8, padding: "0.4rem 0.6rem",
  fontSize: "0.75rem", color: "#d1d5db",
};

const aiBubble: React.CSSProperties = {
  background: "#0f1512", border: "1px solid #1f2a24", borderRadius: 8,
  padding: "0.4rem 0.6rem", fontSize: "0.75rem", color: "#d1d5db",
};

const btn: React.CSSProperties = {
  display: "inline-flex", alignItems: "center", gap: 5, padding: "0.35rem 0.7rem",
  background: "#171717", color: "#ddd", border: "1px solid #2a2a2a",
  borderRadius: 8, cursor: "pointer", fontSize: "0.75rem",
};

const field: React.CSSProperties = {
  width: "100%", background: "#111", color: "#eee", border: "1px solid #2f2f2f",
  borderRadius: 8, padding: "0.4rem 0.55rem", fontSize: "0.76rem", boxSizing: "border-box",
};
