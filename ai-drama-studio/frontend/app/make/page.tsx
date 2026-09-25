"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import TopNav from "@/components/TopNav";
import { API_BASE, api, sseUrl } from "@/lib/api";

/**
 * 一键成片。
 *
 * 目标：从「一句创意」到「一条能发的成片」只点一次按钮。
 * 之前要跑通整条链路得去流水线页手动连 8 个节点、还得先配好 provider；
 * 这里把参数收敛成几个选项，其余全部走后端默认值，
 * 运行过程用 SSE 摊平成阶段进度条，结束直接把成片放出来。
 *
 * 工作流可选（真人短剧 / 真人剧写实电影感 / AI 动漫）。三条线的关键节点 id 刻意保持一致，
 * 所以下面 start() 里的 overrides 一套通用；各线多出的阶段与画风候选都收在 LINES 表里。
 */

type NodeStatus = "pending" | "running" | "completed" | "failed" | "skipped";

/**
 * 工作流选择器。节点 id 在三条线里刻意保持一致
 * （script_planner / shot_images / shot_videos / narration / final_cut），
 * 下面的 overrides 才能一套通用；各线多出来的阶段列在自己的 stages 里。
 */
const WORKFLOWS = [
  { id: "drama-pro", label: "真人短剧（快）", hint: "八段流水线，一步到位，不锁摄影规格" },
  { id: "realistic-drama", label: "真人剧（写实电影感）", hint: "摄影规格 + 定妆照回填档案库 + 按角色分音色 + 口型同步" },
  { id: "anime-drama", label: "AI 动漫（漫剧）", hint: "画风圣经锁风格 + 定妆图 + 逐镜角色路由" },
  { id: "video-restyle", label: "片段魔改（换世界观）", hint: "上传一条现成视频 → 逐镜反推 → 换成宝莱坞/港片/锡兰…重绘，镜头不动" },
];

const STAGES: { id: string; label: string }[] = [
  { id: "script_planner", label: "剧本大纲" },
  { id: "character_generator", label: "人物设定" },
  { id: "scene_generator", label: "场景设定" },
  { id: "storyboard_generator", label: "分镜脚本" },
  { id: "shot_images", label: "分镜图" },
  { id: "narration", label: "配音" },
  { id: "shot_videos", label: "视频片段" },
  { id: "final_cut", label: "合成成片" },
];

const ANIME_STAGES: { id: string; label: string }[] = [
  { id: "script_planner", label: "动漫剧本" },
  { id: "style_bible", label: "画风圣经" },
  { id: "character_generator", label: "角色人设" },
  { id: "character_assets", label: "定妆图" },
  { id: "scene_generator", label: "场景设定" },
  { id: "scene_assets", label: "背景板" },
  { id: "storyboard_generator", label: "分镜脚本" },
  { id: "shot_images", label: "分镜首帧" },
  { id: "shot_end_frames", label: "分镜尾帧（默认关）" },
  { id: "narration", label: "配音" },
  { id: "shot_videos", label: "视频片段" },
  { id: "final_cut", label: "合成成片" },
];

const PRESETS = [
  "外卖小哥被豪门千金当众羞辱，转身亮出集团继承人身份",
  "落魄赘婿被全家嫌弃，三年后带着百亿资产归来",
  "村姑进城打工被同事嘲笑，其实她是隐退的商业女王",
  "被退婚的废柴少年觉醒上古血脉，全场跪拜",
  "战乱年代，柔弱绣娘用一根银针救下敌军统帅",
];

const ANIME_PRESETS = [
  "少年在毕业礼上被判定零天赋，当晚体内的封印之瞳睁开",
  "末世最后一名治愈师，被追杀她的机械体救了",
  "古代女将军穿成现代体育老师的同桌，天天想拔刀",
  "厨神退休后转生成食堂扫地机器人，只为一碗汤",
  "魔法学院的留级生其实是被封印的魔王本体",
];

const STYLE_OPTIONS = [
  { id: "电影感写实，冷色调，浅景深", label: "现代写实" },
  { id: "日系动漫，赛璐璐上色，明亮通透", label: "日系动漫" },
  { id: "国风水墨，青绿山水，留白构图", label: "国风古韵" },
  { id: "暗黑悬疑，高对比度，阴郁光影", label: "暗黑悬疑" },
  { id: "轻喜剧，高饱和暖色，柔和打光", label: "轻喜剧" },
];

// 动漫线的画风选项直接对齐 anime_style.yaml 里那张「画风基底」表，
// 用户在这儿选的词会被写进 {{style}}，画风圣经节点照它定 style_token。
const ANIME_STYLE_OPTIONS = [
  { id: "日漫赛璐璐，硬边色块，均匀描线，高对比", label: "日漫赛璐璐" },
  { id: "3D 国漫渲染，厚涂质感，电影级光", label: "3D 国漫" },
  { id: "国风条漫，工笔线条，淡彩留白", label: "国风条漫" },
  { id: "日系二次元漫剧，大眼小脸，柔和高光", label: "二次元漫剧" },
  { id: "复古港漫，粗黑描线，高对比网点", label: "复古港漫" },
  { id: "水彩叙事，湿边晕染，低饱和", label: "水彩叙事" },
];

/**
 * 真人剧（写实电影感）线：多出的定妆照节点会把成图自动登记进角色档案库，
 * 所以后面的每一镜都会带着这张脸去生图 —— 这是它和「真人短剧（快）」最大的差别。
 * lipsync（口型同步）出厂关闭，要本机装好 ComfyUI + InfiniteTalk 才有意义。
 */
const REAL_STAGES: { id: string; label: string }[] = [
  { id: "script_planner", label: "剧本大纲" },
  { id: "style_bible", label: "摄影规格" },
  { id: "character_generator", label: "选角卡" },
  { id: "character_assets", label: "定妆照（自动入库）" },
  { id: "scene_generator", label: "实景卡" },
  { id: "scene_assets", label: "空镜" },
  { id: "storyboard_generator", label: "分镜+对白" },
  { id: "shot_images", label: "分镜首帧" },
  { id: "shot_end_frames", label: "分镜尾帧（默认关）" },
  { id: "narration", label: "分角色配音" },
  { id: "shot_videos", label: "视频片段" },
  { id: "lipsync", label: "口型同步（默认关）" },
  { id: "final_cut", label: "合成成片" },
];

const REAL_PRESETS = [
  "女儿婚礼当天，母亲拿出一张三十年前的汇款单，全场安静",
  "被裁员的当晚，前老板打来电话：你签字那份文件救了她",
  "合租屋房东上门收房，看见墙上贴着十年前的全家福",
  "离婚手续办完那天，前夫在她租的隔断间门口站到天亮",
  "婆婆把体检报告换了，儿媳在饭桌上一句话没说把碗收了",
];

// 写实线的画风选项对齐 real_style.yaml 的六项摄影规格（媒介/镜头/光圈/布光/调色/质感），
// 这里给的意向会被写进 {{style}}，摄影规格节点照它定 style_token。
const REAL_STYLE_OPTIONS = [
  { id: "电影感写实，35mm 胶片，冷调窗光，浅景深", label: "电影写实" },
  { id: "手持纪实，自然散射光，低对比，轻微晃动", label: "手持纪实" },
  { id: "夜景霓虹，青橙调色，湿地面反光，硬侧光", label: "霓虹夜色" },
  { id: "室内暖调，钨丝灯单一光源，柔和阴影，胶片颗粒", label: "室内暖调" },
  { id: "悬疑冷调，单侧硬光，深阴影，去饱和", label: "悬疑冷调" },
  { id: "年代戏，16mm 胶片，低饱和，自然光，旧物陈设", label: "年代戏" },
];

/**
 * 片段魔改线：不写新剧本，输入是一条**现成的视频**。
 * 镜头数不由这里的滑块决定，而是 input_clip.frames（抽几帧就反推出几镜），
 * 所以这一行没有「④ 镜头数」；配音节点也不存在 —— 原片音轨直接垫进成片。
 */
const RESTYLE_STAGES: { id: string; label: string }[] = [
  { id: "input_clip", label: "读入源视频 + 抽关键帧" },
  { id: "reverse_prompt", label: "逐镜反推（视觉模型看帧）" },
  { id: "style_bible", label: "世界观圣经（替换清单）" },
  { id: "shot_images", label: "逐镜重绘 + 双轴质检" },
  { id: "shot_videos", label: "图生视频" },
  { id: "video_edit", label: "整段改风格（默认关）" },
  { id: "final_cut", label: "合成成片（垫原音轨）" },
];

/** 目标风格库：GET /api/system/restyle_presets，来自 config/restyle_presets.json。 */
type RestylePreset = {
  id: string;
  label: string;
  category: string;
  era_region: string;
  signature: string;
  positive: string;
  style_token: string;
  negative: string;
  displacement: string[];
  camera: string;
  audio: string;
  best_for: string;
};

// 魔改线的「画面风格」由目标风格预设带，这里不再让用户选一遍，否则两个来源会打架。
const RESTYLE_STYLE_OPTIONS = [{ id: "", label: "（由上面的目标风格预设决定）" }];

/**
 * 每条线的差异都收在这张表里：阶段、画风候选、创意预设，
 * 以及两个开关 —— multiVoice（按角色分音色，UI 上就不该只让用户选一个嗓子）、
 * hasAssetNodes（有定妆图/空镜这类资产节点，需要跟着画幅改尺寸）。
 */
const LINES: Record<string, {
  stages: { id: string; label: string }[];
  styles: { id: string; label: string }[];
  presets: string[];
  multiVoice?: boolean;
  hasAssetNodes?: boolean;
  needsSourceVideo?: boolean;
}> = {
  "drama-pro": { stages: STAGES, styles: STYLE_OPTIONS, presets: PRESETS },
  "realistic-drama": {
    stages: REAL_STAGES, styles: REAL_STYLE_OPTIONS, presets: REAL_PRESETS,
    multiVoice: true, hasAssetNodes: true,
  },
  "anime-drama": {
    stages: ANIME_STAGES, styles: ANIME_STYLE_OPTIONS, presets: ANIME_PRESETS,
    hasAssetNodes: true,
  },
  "video-restyle": {
    stages: RESTYLE_STAGES, styles: RESTYLE_STYLE_OPTIONS, presets: [],
    needsSourceVideo: true,
  },
};
const DEFAULT_LINE = LINES["drama-pro"];

/**
 * 生图用 1024x1024 再塞进 720x1280，会留下一大片模糊垫底；
 * 直接按 768x1344 生成（同样是 9:16），成片几乎没有黑边。
 */
const ASPECTS = [
  { id: "vertical", label: "竖屏 9:16", width: 720, height: 1280, imgW: 768, imgH: 1344, hint: "抖音 / 红果 / 番茄" },
  { id: "horizontal", label: "横屏 16:9", width: 1280, height: 720, imgW: 1344, imgH: 768, hint: "B站 / YouTube" },
];

type Artifact = {
  node_id: string;
  kind: "image" | "video";
  url: string;
  name: string;
  size: number;
};

type StageState = { status: NodeStatus; progress: number; note: string; error?: string };

export default function MakePage() {
  const [idea, setIdea] = useState("");
  const [workflow, setWorkflow] = useState(WORKFLOWS[0].id);
  const [aspect, setAspect] = useState("vertical");
  const [shots, setShots] = useState(6);
  const [style, setStyle] = useState(STYLE_OPTIONS[0].id);
  const [voice, setVoice] = useState("zh-CN-YunxiNeural");
  const [voiceOptions, setVoiceOptions] = useState<{ name: string; gender: string }[]>([]);
  const [withVoice, setWithVoice] = useState(true);
  const [withSubtitle, setWithSubtitle] = useState(true);
  const [seconds, setSeconds] = useState(5);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [titleCard, setTitleCard] = useState(true);
  const [endCard, setEndCard] = useState("未完待续");
  const [endCardSub, setEndCardSub] = useState("关注我，看下一集");
  const [trimBadge, setTrimBadge] = useState(true);
  const [logoId, setLogoId] = useState("");
  const [uploading, setUploading] = useState(false);
  // 片段魔改线：源视频（asset id）+ 目标风格预设
  const [sourceId, setSourceId] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [presetId, setPresetId] = useState("");
  const [restylePresets, setRestylePresets] = useState<RestylePreset[]>([]);

  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState("");
  const [stages, setStages] = useState<Record<string, StageState>>({});
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [finalUrl, setFinalUrl] = useState("");
  const [gallery, setGallery] = useState<Artifact[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const lastEventRef = useRef<number>(0);
  const warnedRef = useRef(false);
  // 候选关键帧确认门：后端先出前几张，人工看过了再批量生图
  const [awaiting, setAwaiting] = useState<{ node: string; done: number; total: number } | null>(null);
  const [stalled, setStalled] = useState(false);
  const [previewGate, setPreviewGate] = useState(true);

  // 当前画幅（生图尺寸与成片尺寸同比），缩略图也按它来显示，避免裁边
  const box = ASPECTS.find((a) => a.id === aspect)!;
  const line = LINES[workflow] ?? DEFAULT_LINE;
  const stageList = line.stages;
  const styleOptions = line.styles;
  const presets = line.presets;
  const preset = restylePresets.find((p) => p.id === presetId);

  /** 换工作流时把画风选项跟到对应那套，否则会把「现代写实」塞进动漫的画风圣经。 */
  const pickWorkflow = (id: string) => {
    setWorkflow(id);
    const first = (LINES[id] ?? DEFAULT_LINE).styles[0];
    if (first) setStyle(first.id);
  };

  useEffect(() => {
    api<{ presets: RestylePreset[] }>("/api/system/restyle_presets")
      .then((r) => {
        const list = r.presets || [];
        setRestylePresets(list);
        if (list.length && !list.some((p) => p.id === presetId)) setPresetId(list[0].id);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    api<{ voices: { name: string; gender: string }[] }>("/api/system/tts/voices")
      .then((r) => {
        const zh = r.voices || [];
        setVoiceOptions(zh);
        const male = zh.find((v) => v.name.includes("Yunxi"));
        if (male) setVoice(male.name);
      })
      .catch(() => {});
    // 记住上一次的 run id：刷新页面或后端重启后，还能「断点续跑」接着跑
    try {
      const last = sessionStorage.getItem("lastRunId");
      if (last) setRunId(last);
    } catch {}
    return () => esRef.current?.close();
  }, []);

  const pushLog = useCallback((line: string) => {
    setLogs((prev) => [...prev.slice(-200), line]);
  }, []);

  const patchStage = useCallback((id: string, patch: Partial<StageState>) => {
    setStages((prev) => {
      const base: StageState = prev[id] || { status: "pending", progress: 0, note: "" };
      return { ...prev, [id]: { ...base, ...patch } };
    });
  }, []);

  const loadArtifacts = useCallback(async (rid: string) => {
    try {
      const { artifacts } = await api<{ artifacts: Artifact[] }>(`/api/runs/${rid}/artifacts`);
      setGallery(artifacts.filter((a) => a.kind === "image"));
      const final =
        artifacts.find((a) => a.kind === "video" && a.name.includes("final")) ||
        artifacts.find((a) => a.kind === "video");
      if (final) setFinalUrl(`${API_BASE}${final.url}`);
    } catch (e: any) {
      pushLog(`读取产物失败：${e.message}`);
    }
  }, [pushLog]);

  /**
   * 看门狗：跑长任务时几分钟没进度是常态（供应商在排队），但后端要是无声退出，
   * 进度条就会一动不动地挂着。静默超过阈值就去探一次活，明确告诉用户
   * 「还在跑」还是「已经死了、可以断点续跑」。
   */
  useEffect(() => {
    if (!running) {
      setStalled(false);
      return;
    }
    lastEventRef.current = Date.now();
    warnedRef.current = false;
    const timer = setInterval(async () => {
      const silent = Date.now() - lastEventRef.current;
      if (silent < 45000) return;
      try {
        await api<{ ok: boolean }>("/api/system/ping");
        if (!warnedRef.current) {
          warnedRef.current = true;
          setStalled(true);
          pushLog(`⚠️ 已 ${Math.round(silent / 1000)} 秒没有新进度（后端仍在运行，等供应商返回）`);
        }
      } catch {
        setRunning(false);
        setStalled(false);
        pushLog("✘ 后端已停止响应；已完成的镜头都留着，点「断点续跑」接着跑");
      }
    }, 10000);
    return () => clearInterval(timer);
  }, [running, pushLog]);

  const handleEvent = useCallback(
    (ev: any) => {
      const id = ev.node_id;
      switch (ev.type) {
        case "run_start":
          pushLog(`开始运行，共 ${(ev.nodes || []).length} 个节点`);
          break;
        case "node_start":
          if (id) patchStage(id, { status: "running", progress: 0 });
          pushLog(`▶ ${id} 开始`);
          break;
        case "node_progress":
          if (id && ev.status !== undefined) patchStage(id, { note: String(ev.status) });
          if (id && typeof ev.value === "number" && ev.max) {
            patchStage(id, { progress: Math.round((ev.value / ev.max) * 100) });
          }
          break;
        case "node_waiting_approval":
          if (id) {
            patchStage(id, { status: "running", note: `候选 ${ev.done}/${ev.total} 张，等你确认` });
            setAwaiting({ node: id, done: Number(ev.done) || 0, total: Number(ev.total) || 0 });
            pushLog(`⏸ ${id} 先出 ${ev.done} 张候选关键帧，看过再决定要不要继续`);
            setRunning(false);
          }
          break;
        case "item_done":
          if (id) {
            patchStage(id, {
              progress: ev.progress ?? 0,
              note: `${ev.index + 1}/${ev.total}`,
            });
          }
          break;
        case "node_retry":
          if (id) patchStage(id, { note: `第 ${ev.attempt} 次重试…` });
          pushLog(`⚠ ${id} 重试（${ev.error}）`);
          break;
        case "node_done":
          if (id) {
            patchStage(id, {
              status: ev.skipped ? "skipped" : "completed",
              progress: 100,
              note: ev.skipped ? "已跳过" : "",
            });
          }
          pushLog(`✔ ${id} 完成`);
          break;
        case "node_error":
          if (id) patchStage(id, { status: "failed", error: ev.error });
          pushLog(`✘ ${id} 失败：${ev.error}`);
          break;
        case "run_end":
          pushLog(`运行结束：${ev.status}`);
          if (ev.error) setError(String(ev.error));
          break;
        default:
          break;
      }
    },
    [patchStage, pushLog],
  );

  /** 把 SSE 挂到某个 run 上，start 和 resume 共用一套进度处理。 */
  const attach = (rid: string) => {
    const es = new EventSource(sseUrl(rid));
    esRef.current = es;
    es.onmessage = (e) => {
      let ev: any;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      lastEventRef.current = Date.now();
      warnedRef.current = false;
      setStalled(false);
      handleEvent(ev);
      // run_end = 跑完；node_waiting_approval = 关键帧预览门，两种都要把产物拉出来
      if (ev.type === "run_end" || ev.type === "node_waiting_approval") {
        es.close();
        esRef.current = null;
        setRunning(false);
        void loadArtifacts(rid);
      }
    };
    es.onerror = () => {
      // 后端断开（如崩溃）时不要静默卡住
      pushLog("SSE 连接中断，正在尝试读取当前进度…");
      setRunning(false);
      es.close();
      esRef.current = null;
      void loadArtifacts(rid);
    };
  };

  /**
   * 断点续跑：后端给每个镜头都存了 checkpoint，这里只重跑上次没跑完的部分。
   * 30 镜里第 28 镜挂了，不用把前 27 镜重新生成一遍（省钱也省时间）。
   */
  const resume = async () => {
    if (!runId) return;
    setError("");
    setLogs([]);
    setAwaiting(null);
    setRunning(true);
    try {
      await api(`/api/runs/${runId}/resume`, { method: "POST", body: JSON.stringify({}) });
      pushLog(`续跑 ${runId}：已完成的镜头直接复用，只补没跑完的`);
      attach(runId);
    } catch (e: any) {
      setError(`续跑失败：${e.message}`);
      setRunning(false);
    }
  };

  const start = async () => {
    if (!idea.trim()) {
      setError("先写一句创意");
      return;
    }
    if (line.needsSourceVideo) {
      if (!sourceId) {
        setError("这条线要先把「被魔改的那条视频」传上来（右上角高级选项里，或直接拖 mp4）");
        return;
      }
      if (!preset) {
        setError("先选一个目标风格（宝莱坞 / 八十年代港片 / 维伦纽瓦…）");
        return;
      }
    }
    setError("");
    setFinalUrl("");
    setGallery([]);
    setLogs([]);
    setStages({});
    setRunning(true);

    const overrides: Record<string, Record<string, unknown>> = {
      final_cut: {
        width: box.width,
        height: box.height,
        burn_subtitle: withSubtitle,
        // 片头文案默认取剧本节点；魔改线没有剧本节点，就用用户写的那句说明
        title_card: titleCard ? (line.needsSourceVideo ? idea.trim() : "{{script_planner.title}}") : "",
        end_card: endCard || "",
        end_card_sub: endCardSub || "",
        trim_badge: trimBadge,
        ...(logoId ? { logo_asset_id: logoId } : {}),
      },
      // 按角色分音色的线（真人剧）不能让用户选的单一嗓子盖掉角色表，
      // 这里只在非 multiVoice 的线上锁 voice；关掉配音两条线都整个跳过。
      narration: withVoice ? (line.multiVoice ? {} : { voice }) : { disabled: true },
      shot_videos: { seconds },
      // 生图和成片同画幅，避免竖屏成片出现大面积模糊垫底
      shot_images: {
        width: box.imgW,
        height: box.imgH,
        // 关键帧预览门：先出 2 张候选，看过再批量生图；关掉就是一路到底
        preview_gate: previewGate ? 2 : 0,
      },
      // 有资产节点的线（动漫 / 真人剧）：背景板与尾帧跟成片同画幅（定妆图始终竖构图，不跟）。
      // shot_end_frames 出厂是关的，这里先把尺寸配好，用户在工作流页打开它就已是正确画幅。
      ...(line.hasAssetNodes
        ? {
            scene_assets: { width: box.imgW, height: box.imgH },
            shot_end_frames: { width: box.imgW, height: box.imgH, preview_gate: previewGate ? 2 : 0 },
          }
        : {}),
    };
    // 后端对不存在的节点 id 直接 400，所以按这条线真实有的节点过滤一遍
    // （阶段表就是节点表：进度条上看不到的节点，也不该收到这里的覆盖）。
    const known = new Set(stageList.map((s) => s.id));
    for (const key of Object.keys(overrides)) {
      if (!known.has(key)) delete overrides[key];
    }

    try {
      const r = await api<{ run_id: string }>("/api/runs/", {
        method: "POST",
        body: JSON.stringify({
          workflow,
          input: {
            title: idea.trim(),
            genre: "",
            duration: `${shots * seconds} 秒`,
            style,
            shots,
            episodes: 1,
            voice,
            // 魔改线：源视频走素材库 id（引擎按 asset id / /media/assets 链接 / 本地路径三种写法解析），
            // 目标风格的各个字段必须摊平成 preset_* 传进去 —— 世界观圣经模板只认扁平键。
            ...(line.needsSourceVideo && preset
              ? {
                  source_video: sourceId,
                  preset_label: preset.label,
                  preset_era_region: preset.era_region,
                  preset_signature: preset.signature,
                  preset_positive: preset.positive,
                  preset_style_token: preset.style_token,
                  preset_camera: preset.camera,
                  preset_audio: preset.audio,
                  preset_displacement: preset.displacement,
                }
              : {}),
          },
          overrides,
        }),
      });
      setRunId(r.run_id);
      try {
        sessionStorage.setItem("lastRunId", r.run_id);
      } catch {}
      attach(r.run_id);
    } catch (e: any) {
      setError(e.message || "启动失败");
      setRunning(false);
    }
  };

  /** LOGO 上传到资产库，后端按 asset_id 取本地路径，避免前端传绝对路径。 */
  const uploadLogo = async (e: React.ChangeEvent<HTMLInputElement>) => {    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("tags", "logo");
      const res = await fetch(`${API_BASE}/api/assets/upload`, { method: "POST", body: fd });
      const data = await res.json();
      if (data?.id) setLogoId(data.id);
    } catch {
      setError("LOGO 上传失败");
    } finally {
      setUploading(false);
    }
  };

  /**
   * 源视频也走素材库：video_input 节点认 asset id，后端拿它去 data/assets 找文件。
   * 不支持贴直链 —— 要先落到本机才能 ffprobe 和抽帧，所以宁可不给。
   */
  const uploadSource = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("tags", "restyle-source");
      const res = await fetch(`${API_BASE}/api/assets/upload`, { method: "POST", body: fd });
      const data = await res.json();
      if (!data?.id) throw new Error(data?.detail || "上传接口没返回 id");
      setSourceId(data.id);
      setSourceName(file.name);
    } catch (err: any) {
      setError(`源视频上传失败：${err.message}`);
    } finally {
      setUploading(false);
    }
  };

  const cancel = async () => {
    if (!runId) return;
    try {
      await api(`/api/runs/${runId}/cancel`, { method: "POST" });
      pushLog("已请求取消");
    } catch (e: any) {
      pushLog(`取消失败：${e.message}`);
    }
  };

  const done = Object.values(stages).filter((s) => s.status === "completed").length;
  const overallPct = Math.round((done / stageList.length) * 100);

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#fff" }}>
      <TopNav title="一键成片" subtitle="填一句创意 → 自动出片（剧本·分镜·生图·配音·字幕·合成）" />

      <div style={{ maxWidth: 1180, margin: "0 auto", padding: "1.5rem 1.25rem 4rem" }}>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1fr) minmax(320px, 1.1fr)", gap: 20 }}>
          {/* ---------------- 左侧：参数 ---------------- */}
          <section style={card}>
            <h2 style={sectionTitle}>① 用哪条流水线</h2>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {WORKFLOWS.map((w) => (
                <button
                  key={w.id}
                  onClick={() => pickWorkflow(w.id)}
                  style={{ ...option, ...(workflow === w.id ? optionOn : {}), textAlign: "left" }}
                  title={w.hint}
                >
                  <div style={{ fontWeight: 600 }}>{w.label}</div>
                  <div style={{ fontSize: "0.7rem", color: "#888", marginTop: 2 }}>{w.hint}</div>
                </button>
              ))}
            </div>

            <h2 style={{ ...sectionTitle, marginTop: 22 }}>
              {line.needsSourceVideo ? "② 这条素材拍的是什么（给反推节点的说明）" : "② 你想要什么故事"}
            </h2>
            <textarea
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              placeholder={
                line.needsSourceVideo
                  ? "例如：两个人在写字楼会议室里吵架，最后一个人摔门出去 —— 会写进反推提示词，帮视觉模型对齐它看到的画面"
                  : "例如：外卖小哥被豪门千金当众羞辱，转身亮出集团继承人身份"
              }
              rows={3}
              style={{ ...input, resize: "vertical", lineHeight: 1.6 }}
            />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
              {presets.map((p) => (
                <button key={p} onClick={() => setIdea(p)} style={chip} title={p}>
                  {p.slice(0, 12)}…
                </button>
              ))}
            </div>

            {line.needsSourceVideo && (
              <div style={{ marginTop: 12 }}>
                <label style={label}>源视频（必须先传，这条线不写新剧本）</label>
                <input type="file" accept="video/*" onChange={uploadSource} disabled={uploading}
                  style={{ ...input, padding: "0.45rem", fontSize: "0.78rem" }} />
                <p style={{ color: "#6b7280", fontSize: "0.72rem", margin: "6px 0 0", lineHeight: 1.6 }}>
                  {uploading
                    ? "上传中…"
                    : sourceId
                      ? `已选：${sourceName}（asset ${sourceId.slice(0, 8)}…）。引擎会先 ffprobe + 抽 4 张关键帧`
                      : "上传后存进素材库；本机要装好 ffmpeg，否则抽帧这一步会直接报错告诉你去哪配。暂不支持贴网络直链"}
                </p>

                <label style={{ ...label, marginTop: 12 }}>目标风格（「假如这条片子是 ___ 拍的」）</label>
                <select value={presetId} onChange={(e) => setPresetId(e.target.value)} style={input}>
                  {restylePresets.length === 0 && (
                    <option value="">（读不到 config/restyle_presets.json）</option>
                  )}
                  {restylePresets.map((p) => (
                    <option key={p.id} value={p.id}>
                      [{p.category}] {p.label} —— {p.era_region}
                    </option>
                  ))}
                </select>
                {preset && (
                  <div style={{
                    marginTop: 8, padding: "10px 12px", background: "#111",
                    border: "1px solid #262626", borderRadius: 8,
                    fontSize: "0.74rem", color: "#9ca3af", lineHeight: 1.7,
                  }}>
                    <div><b style={{ color: "#e5e7eb" }}>一眼可辨：</b>{preset.signature}</div>
                    <div style={{ marginTop: 4 }}><b style={{ color: "#e5e7eb" }}>替换清单：</b>
                      {preset.displacement.slice(0, 3).join("；")}
                      {preset.displacement.length > 3 ? ` …共 ${preset.displacement.length} 条` : ""}
                    </div>
                    <div style={{ marginTop: 4 }}><b style={{ color: "#e5e7eb" }}>适合：</b>{preset.best_for}</div>
                    <div style={{ marginTop: 4, color: "#6b7280" }}>
                      音轨：{preset.audio}
                    </div>
                  </div>
                )}
                <p style={{ color: "#6b7280", fontSize: "0.72rem", margin: "8px 0 0", lineHeight: 1.6 }}>
                  这几项会摊平成 preset_label / preset_style_token / preset_displacement … 传进工作流，
                  世界观圣经节点照着它们出替换清单。想换一个说法，去流水线页用聊天改
                  style_bible 节点的提示词模板 restyle_bible.yaml。
                </p>
              </div>
            )}

            <h2 style={{ ...sectionTitle, marginTop: 22 }}>③ 画幅</h2>
            <div style={{ display: "flex", gap: 8 }}>
              {ASPECTS.map((a) => (
                <button
                  key={a.id}
                  onClick={() => setAspect(a.id)}
                  style={{ ...option, ...(aspect === a.id ? optionOn : {}) }}
                >
                  <div style={{ fontWeight: 600 }}>{a.label}</div>
                  <div style={{ fontSize: "0.7rem", color: "#888", marginTop: 2 }}>{a.hint}</div>
                </button>
              ))}
            </div>

            {line.needsSourceVideo ? (
              <>
                <h2 style={{ ...sectionTitle, marginTop: 22 }}>④ 出几镜</h2>
                <p style={{ color: "#9ca3af", fontSize: "0.75rem", lineHeight: 1.7, margin: 0 }}>
                  这条线的镜头数 = 源视频抽的帧数（出厂 4）。反推节点只会看它拿到的前 4 张帧，
                  所以 frames 写 6 也只会出 4 镜 —— 想改到 8 镜，去流水线页说
                  「把 input_clip 的 frames 改成 8，反推节点一次看 8 张」，
                  同时把 input_clip.frames 与 reverse_prompt 的取帧上限一起调。
                  每镜时长跟着下面的「每镜时长」走（图生视频 3-12 秒）。
                </p>
              </>
            ) : (
              <>
                <h2 style={{ ...sectionTitle, marginTop: 22 }}>④ 镜头数</h2>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {[4, 6, 8, 10].map((n) => (
                    <button key={n} onClick={() => setShots(n)} style={{ ...option, ...(shots === n ? optionOn : {}) }}>
                      {n} 镜
                    </button>
                  ))}
                  <span style={{ alignSelf: "center", fontSize: "0.75rem", color: "#666" }}>
                    约 {Math.round((shots * seconds) / 60)} 分钟
                  </span>
                </div>
              </>
            )}

            <h2 style={{ ...sectionTitle, marginTop: 22 }}>⑤ 画面风格</h2>
            <select value={style} onChange={(e) => setStyle(e.target.value)} style={input}>
              {styleOptions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label} —— {s.id}
                </option>
              ))}
            </select>

            <label style={toggleRow}>
              <input type="checkbox" checked={withVoice} onChange={(e) => setWithVoice(e.target.checked)} />
              AI 配音（edge-tts 免费）
            </label>
            <label style={toggleRow}>
              <input type="checkbox" checked={withSubtitle} onChange={(e) => setWithSubtitle(e.target.checked)} />
              烧入字幕
            </label>

            <button onClick={() => setShowAdvanced((v) => !v)} style={linkBtn}>
              {showAdvanced ? "收起高级选项 ▲" : "高级选项 ▼"}
            </button>
            {showAdvanced && (
              <div style={{ marginTop: 10 }}>
                {withVoice && (
                  <div style={{ marginBottom: 12 }}>
                    <label style={label}>{line.multiVoice ? "旁白 / 兜底音色" : "配音音色"}</label>
                    <select value={voice} onChange={(e) => setVoice(e.target.value)} style={input}>
                      {voiceOptions.length === 0 && <option value={voice}>{voice}</option>}
                      {voiceOptions.map((v) => (
                        <option key={v.name} value={v.name}>
                          {v.name}（{v.gender === "Male" ? "男" : "女"}）
                        </option>
                      ))}
                    </select>
                    {line.multiVoice && (
                      <p style={{ margin: "6px 0 0", color: "#888", fontSize: "0.72rem", lineHeight: 1.6 }}>
                        真人剧按角色自动分音色：选角卡里每个角色各带一个 voice，配音节点按说话人取，
                        查不到的角色从音色池里稳定分配。这里选的只用作旁白和兜底。
                        想指定谁用哪个嗓子，去流水线页让 AI 改 narration 节点的 voice_map。
                      </p>
                    )}
                  </div>
                )}
                <div>
                  <label style={label}>每镜时长</label>
                  <select value={seconds} onChange={(e) => setSeconds(Number(e.target.value))} style={input}>
                    {[3, 4, 5, 8, 10].map((s) => (
                      <option key={s} value={s}>
                        {s} 秒
                      </option>
                    ))}
                  </select>
                </div>

                <label style={toggleRow}>
                  <input type="checkbox" checked={titleCard} onChange={(e) => setTitleCard(e.target.checked)} />
                  加片头（用剧本自动生成的剧名）
                </label>
                <label style={toggleRow}>
                  <input type="checkbox" checked={trimBadge} onChange={(e) => setTrimBadge(e.target.checked)} />
                  抹掉源视频右下角「AI生成」角标
                </label>

                <div style={{ marginTop: 12 }}>
                  <label style={label}>片尾主文案（留空则不加片尾）</label>
                  <input value={endCard} onChange={(e) => setEndCard(e.target.value)}
                    placeholder="未完待续" style={input} />
                  <label style={{ ...label, marginTop: 8 }}>片尾副文案</label>
                  <input value={endCardSub} onChange={(e) => setEndCardSub(e.target.value)}
                    placeholder="关注我，看下一集" style={input} />
                </div>

                <div style={{ marginTop: 12 }}>
                  <label style={label}>角标 LOGO（透明底 PNG 效果最好）</label>
                  <input type="file" accept="image/*" onChange={uploadLogo} disabled={uploading}
                    style={{ ...input, padding: "0.45rem", fontSize: "0.78rem" }} />
                  {logoId && (
                    <button onClick={() => setLogoId("")} style={{ ...linkBtn, marginTop: 6 }}>
                      已设置角标 · 点此移除
                    </button>
                  )}
                  <p style={{ color: "#555", fontSize: "0.7rem", margin: "6px 0 0" }}>
                    {uploading ? "上传中…" : "上传到资产库，之后所有成片可复用同一张"}
                  </p>
                </div>
              </div>
            )}

            <label
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 8,
                marginTop: 14,
                fontSize: "0.76rem",
                color: "#9ca3af",
                lineHeight: 1.5,
                cursor: running ? "not-allowed" : "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={previewGate}
                disabled={running}
                onChange={(e) => setPreviewGate(e.target.checked)}
                style={{ marginTop: 2 }}
              />
              先出 2 张候选关键帧，确认后再批量生图
              <span style={{ color: "#6b7280" }}>（批量生图最贵，先选优能省掉大量废片）</span>
            </label>

            <div style={{ display: "flex", gap: 10, marginTop: 20, flexWrap: "wrap" }}>
              <button onClick={start} disabled={running} style={primaryBtn(running)}>
                {running ? `生成中… ${overallPct}%` : "开始生成"}
              </button>
              {running && (
                <button onClick={cancel} style={ghostBtn}>
                  取消
                </button>
              )}
              {awaiting && !running && (
                <button
                  onClick={resume}
                  style={primaryBtn(false)}
                  title="确认候选关键帧，接着生成剩下的镜头（已出的不会重跑）"
                >
                  确认并继续（{awaiting.done}/{awaiting.total}）
                </button>
              )}
              {!running && runId && !awaiting && (
                <button onClick={resume} style={ghostBtn} title="只重跑上次没跑完的镜头，已完成的直接复用">
                  断点续跑
                </button>
              )}
            </div>
            {stalled && running && (
              <p style={{ color: "#f59e0b", fontSize: "0.75rem", marginTop: 10 }}>
                长时间没有新进度属正常（供应商排队），后端仍在运行，不用中断。
              </p>
            )}
            {error && (
              <div style={errBox}>
                {error}
                {error.includes("Key") && (
                  <>
                    {" "}
                    <Link href="/settings" style={{ color: "#c7d2fe" }}>
                      去设置页填 Key →
                    </Link>
                  </>
                )}
              </div>
            )}
            {!running && !finalUrl && !error && (
              <p style={{ color: "#555", fontSize: "0.75rem", marginTop: 12, lineHeight: 1.6 }}>
                首次使用建议先到
                <Link href="/settings" style={{ color: "#818cf8" }}>
                  {" "}
                  设置页{" "}
                </Link>
                点「一键自检」，确认 LLM 与生图/生视频通道可用。
              </p>
            )}
          </section>

          {/* ---------------- 右侧：进度 + 成片 ---------------- */}
          <section style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={card}>
              <h2 style={sectionTitle}>制作进度</h2>
              <div style={{ height: 6, background: "#1c1c1c", borderRadius: 999, overflow: "hidden", marginBottom: 14 }}>
                <div
                  style={{
                    height: "100%",
                    width: `${overallPct}%`,
                    background: "linear-gradient(90deg,#6366f1,#8b5cf6)",
                    transition: "width .3s",
                  }}
                />
              </div>
              <div style={{ display: "grid", gap: 6 }}>
                {stageList.map((s, i) => {
                  const st = stages[s.id] || { status: "pending" as NodeStatus, progress: 0, note: "" };
                  return (
                    <div key={s.id} style={stageRow}>
                      <span style={{ width: 22, textAlign: "center", color: statusColor(st.status) }}>
                        {statusIcon(st.status)}
                      </span>
                      <span style={{ width: 26, color: "#4b5563", fontSize: "0.75rem" }}>{String(i + 1).padStart(2, "0")}</span>
                      <span style={{ flex: 1, color: st.status === "pending" ? "#555" : "#ddd", fontSize: "0.85rem" }}>
                        {s.label}
                      </span>
                      <span style={{ fontSize: "0.72rem", color: "#6b7280", maxWidth: 190, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {st.error ? st.error.slice(0, 60) : st.note}
                      </span>
                      {st.status === "running" && <span style={{ fontSize: "0.72rem", color: "#a5b4fc" }}>{st.progress}%</span>}
                    </div>
                  );
                })}
              </div>
            </div>

            {finalUrl && (
              <div style={card}>
                <h2 style={sectionTitle}>成片</h2>
                <video
                  src={finalUrl}
                  controls
                  playsInline
                  style={{
                    width: "100%",
                    // 竖屏成片不要拉满整列宽度，否则播放器比画面宽一大截、左右两条大黑边
                    maxWidth: box.height > box.width ? 360 : "100%",
                    margin: "0 auto",
                    display: "block",
                    borderRadius: 10,
                    background: "#000",
                    maxHeight: 560,
                    objectFit: "contain",
                  }}
                />
                <div style={{ display: "flex", gap: 10, marginTop: 12, flexWrap: "wrap" }}>
                  <a href={finalUrl} download style={{ ...primaryBtn(false), textDecoration: "none", display: "inline-block" }}>
                    下载成片
                  </a>
                  <a href={finalUrl} target="_blank" rel="noreferrer" style={{ ...ghostBtn, textDecoration: "none", display: "inline-block" }}>
                    新标签打开
                  </a>
                  <Link href="/history" style={{ ...ghostBtn, textDecoration: "none", display: "inline-block" }}>
                    去成片库
                  </Link>
                </div>
                <p style={{ color: "#555", fontSize: "0.72rem", margin: "10px 0 0" }}>
                  成片已自动登记进「成片库」，刷新页面也能找到。
                </p>
              </div>
            )}

            {gallery.length > 0 && (
              <div style={card}>
                <h2 style={sectionTitle}>分镜图（{gallery.length}）</h2>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(90px,1fr))", gap: 8 }}>
                  {gallery.map((g) => (
                    <a key={g.url} href={`${API_BASE}${g.url}`} target="_blank" rel="noreferrer">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={`${API_BASE}${g.url}`}
                        alt={g.name}
                        style={{
                          width: "100%",
                          // 与生图同比例，contain 完整显示；竖屏分镜图不会被上下裁掉
                          aspectRatio: `${box.imgW}/${box.imgH}`,
                          objectFit: "contain",
                          background: "#000",
                          borderRadius: 8,
                          border: "1px solid #232323",
                        }}
                      />
                    </a>
                  ))}
                </div>
              </div>
            )}

            {logs.length > 0 && (
              <div style={card}>
                <h2 style={sectionTitle}>运行日志</h2>
                <pre style={logBox}>{logs.join("\n")}</pre>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function statusIcon(s: NodeStatus) {
  return { pending: "○", running: "◐", completed: "●", failed: "✕", skipped: "—" }[s];
}
function statusColor(s: NodeStatus) {
  return { pending: "#374151", running: "#818cf8", completed: "#22c55e", failed: "#ef4444", skipped: "#6b7280" }[s];
}

const card: React.CSSProperties = {
  background: "#111",
  border: "1px solid #1e1e1e",
  borderRadius: 12,
  padding: "1.25rem",
};
const sectionTitle: React.CSSProperties = {
  fontSize: "0.9rem",
  fontWeight: 600,
  marginBottom: 10,
  color: "#a5b4fc",
};
const input: React.CSSProperties = {
  width: "100%",
  padding: "0.7rem",
  background: "#141414",
  border: "1px solid #2a2a2a",
  borderRadius: 8,
  color: "#fff",
  fontSize: "0.9rem",
  outline: "none",
  boxSizing: "border-box",
};
const label: React.CSSProperties = { display: "block", marginBottom: 6, color: "#888", fontSize: "0.78rem" };
const chip: React.CSSProperties = {
  padding: "0.3rem 0.6rem",
  background: "#1a1a1a",
  border: "1px solid #2a2a2a",
  borderRadius: 999,
  color: "#aaa",
  fontSize: "0.72rem",
  cursor: "pointer",
};
const option: React.CSSProperties = {
  flex: "1 1 auto",
  padding: "0.6rem 0.8rem",
  background: "#1a1a1a",
  border: "1px solid #2a2a2a",
  borderRadius: 8,
  color: "#ddd",
  cursor: "pointer",
  fontSize: "0.82rem",
  textAlign: "center",
};
const optionOn: React.CSSProperties = { background: "#6366f133", borderColor: "#6366f1", color: "#fff" };
const toggleRow: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginTop: 12,
  fontSize: "0.85rem",
  color: "#ccc",
};
const linkBtn: React.CSSProperties = {
  marginTop: 14,
  background: "none",
  border: "none",
  color: "#818cf8",
  cursor: "pointer",
  fontSize: "0.8rem",
  padding: 0,
};
const primaryBtn = (busy: boolean): React.CSSProperties => ({
  flex: 1,
  padding: "0.85rem",
  background: busy ? "#333" : "linear-gradient(135deg,#6366f1,#8b5cf6)",
  color: "#fff",
  border: "none",
  borderRadius: 10,
  fontSize: "0.95rem",
  fontWeight: 600,
  cursor: busy ? "not-allowed" : "pointer",
});
const ghostBtn: React.CSSProperties = {
  padding: "0.85rem 1.1rem",
  background: "#1a1a1a",
  border: "1px solid #2a2a2a",
  borderRadius: 10,
  color: "#ddd",
  cursor: "pointer",
  fontSize: "0.9rem",
  textAlign: "center",
};
const errBox: React.CSSProperties = {
  marginTop: 12,
  background: "#7f1d1d33",
  border: "1px solid #7f1d1d",
  color: "#fca5a5",
  padding: "0.7rem",
  borderRadius: 8,
  fontSize: "0.8rem",
  lineHeight: 1.6,
};
const stageRow: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "0.3rem 0",
};
const logBox: React.CSSProperties = {
  maxHeight: 200,
  overflow: "auto",
  background: "#0d0d0d",
  border: "1px solid #1e1e1e",
  borderRadius: 8,
  padding: "0.7rem",
  fontSize: "0.72rem",
  color: "#9ca3af",
  margin: 0,
  whiteSpace: "pre-wrap",
};
