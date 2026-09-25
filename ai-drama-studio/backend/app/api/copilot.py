"""AI 助手：用自然语言改流水线步骤和提示词。

铁律：**只提议，不偷写。** LLM 返回结构化 patch，服务端逐条校验后交给前端展示，
用户点「确认应用」才落盘；落盘前还要过一遍引擎自己的 WorkflowConfig 校验。
每条提案带 base_hash（提案时的工作流文件指纹），期间文件被别处改过就拒绝应用，
避免把别人刚保存的修改静默覆盖掉。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import prompt_store, workflow_store

router = APIRouter(prefix="/api/copilot", tags=["copilot"])

MAX_PROPOSALS = 12
MAX_DOC_BYTES = 60_000

WORKFLOW_ACTIONS = {
    "workflow.node.patch", "workflow.node.add", "workflow.node.remove",
    "workflow.meta.patch", "workflow.create",
}
PROMPT_ACTIONS = {"prompt.patch", "prompt.create"}
ACTIONS = WORKFLOW_ACTIONS | PROMPT_ACTIONS

FIELD_REFERENCE = """节点通用字段（所有 type 都可用）：
- depends_on: 上游节点 id 数组（拓扑排序靠它，不许成环）。渲染上下文里只放直接上游，
  所以引用谁就必须挂在 depends_on 上，漏挂会让提示词里留下原样的 {{...}} 大括号
- foreach: 把节点按数组展开成多次，写法如 "storyboard.shots"（上游输出别名.字段）
- output: 本节点输出的别名，只用于 foreach 的来源书写；下游提示词引用要用节点 id，
  即 {{节点id.字段}}（例 {{script_planner.title}}）
- retry / timeout / concurrency: 重试次数 / 秒数 / foreach 并发数
- disabled: true = 本节点整个跳过，下游拿不到它的产物会自动降级（不做结构改动）
- preview_gate: 整数 N，跑完 N 个 foreach 项后停下等人工确认（省钱门）
- qc: 生图质检 {"enabled":true,"threshold":6,"max_attempts":2,"prompt_template":"xx.yaml"}
- approval_before: true = 跑这个节点前先看一眼参数

各 type 专有字段（**只能用这些，别自己发明字段名**）：
- llm / text：prompt_template（必填，config/prompts/scripts 下的 yaml 文件名）、model、json_mode、
  images_from（某个 video_input 节点 id：挂上它就走视觉模型，把抽出来的关键帧（最多 4 张）
  连同模板一起送进去做「反推现成视频」；此时 model 要填视觉模型，读 VISION_API_KEY/ZHIPU_API_KEY，
  不是 deepseek）、max_tokens（多模态反推时要给足，逐镜记录很容易超 2000 字）。
  注意：llm 节点没有 prompt 字段，正文提示词存在 yaml 模板里，要改用 prompt.* 提案。
- image / comfyui_image：provider、model、prompt、negative_prompt、workflow_file、output_node、
  width、height、steps、cfg、seed、shot_type、micro_action、character_refs(true/false)、
  character_refs_max、images_from、comfy_url、prev_frame_refs、
  init_image_from（魔改线用：把某节点的第 i 张图配给第 i 镜当"被改写的对象"，
  典型来源是 video_input 抽出的关键帧；配不到的那一镜会自动不挂参考图）
  register_character（true=把本节点产出的定妆图登记进角色档案库，之后每一镜都自动拿它当
  参考图；重跑只替换本节点自己生成过的图，用户上传的参考图不动）、
  register_character_max（每个角色最多登记几张，默认 3）、character_name_field（定妆图条目里
  存角色名的字段，默认 name）、character_project（档案库所属项目 id，默认取运行的 project_id）
- video / comfyui_video / agnes_video：provider、model、prompt、negative_prompt、shot_type、
  micro_action、width、height、seconds、fps、seed、mode、resolution、aspect_ratio、
  first_frame_from（取某节点的图当首帧）、last_frame_from（取某节点的图当尾帧，
  只有 seedance / agnes 认首尾帧，别的通道会静默忽略）、
  audio_from（取某节点的音频按镜号配对送给供应商，给口型同步用；
  只有 provider=comfyui 的 InfiniteTalk 一类预设认它，云端通道一律忽略）、
  workflow_file + comfy_url + output_node（provider=comfyui 时指定本地 ComfyUI 预设）、
  source_video_from（取某个 video_input 节点的整段视频或第 i 段切片当「待编辑源片」，
  按 foreach 下标配对；本地路径会先经 PUBLIC_ASSET_BASE_URL 发布成公网 URL 再交给云端）、
  mode 可以是 video_edit（整段改风格，不重新生成运动）：只有 provider=dashscope 且 model
  含 videoedit（如 wan2.7-videoedit）时成立，此时 first_frame_from 的图会作为 reference_image
  一起送进去；ComfyUI 侧要整段编辑就得自己导出 VACE 类预设填 workflow_file。
  打开这种节点后记得把 final_cut 的 from 改成指向它，否则成片里还是旧的 shot_videos。
  provider 可选值：comfyui、seedance、kling、agnes、zhipu、modelscope、dashscope。
  tail_frame_from_prev（true=上一镜尾帧续接）、
  camera_fixed、auto_remove_watermark、generate_audio、filename
- tts：engine(edge|gptsovits)、voice（edge 音色名，作为查不到说话人时的兜底）、
  voice_name（gptsovits 音色库名字）、rate、pitch、text、text_field、filename、
  speaker_field（分镜里存说话人的字段，真人剧用 speaker）、
  voices_from（角色设定节点 id，引擎从中读每个角色的 voice 字段建音色表）、
  voice_map（手写覆盖表 {"角色名":"zh-CN-XiaoxiaoNeural"}，值也可以是
  {"engine":"gptsovits","voice_name":"某克隆音色","rate":"+0%"}）、
  voice_pool（表里查不到的角色从这个音色池按名字稳定分配，保证多人对话不撞声）。
  台词里写「角色名：正文」时，只有角色名确实在音色表里才会被拆出来当说话人，
  旁白开头如「注意：」不会被误伤
- ffmpeg：from（输入表达式）、filename、reencode
- video_input：读入一条**现成的**视频（片段魔改线的入口，它不生产内容）。
  source（素材库 asset id / /media/assets 链接 / 本地路径；运行输入里的 source_video 也可以，
  节点上写了 source 就以节点为准；http 直链不支持）、
  frames（抽几帧，默认 4，上限 12）、frame_times（[0.5, 7.2] 精确到秒，写了就不均匀抽）、
  max_side（帧的最长边像素，压小给多模态模型看）、extract_audio（true=顺手抽出原音轨）、
  clip_seconds（整数 N=按 N 秒切成片段）。
  产物：videos=源视频或切片、images/frames=关键帧、audios=原音轨、
  duration/width/height/fps/has_audio。**没有 paths**，所以引用它时要按 role 选对字段
  （图用 init_image_from，音频用 bgm_from / voices_from）。
- postprocess：合成成片。width、height、fps、crf、fit、from、crossfade、bgm、bgm_gain_db、
  bgm_from（从某个 video_input 节点取原片音轨垫进成片，魔改线用它保住原台词的节奏；
  走这条路时默认 bgm_loop=false —— 成片比源视频长时不会听见台词重播）、bgm_loop、
  voice_gain_db、burn_subtitle、subtitles_from、subtitle_field、voices_from、title_card、
  title_card_sub、end_card、end_card_sub、card_seconds、trim_badge、logo、logo_asset_id、
  logo_pos、logo_scale、logo_opacity、font、font_size、preset、filename
- noop：无字段

镜头类型 shot_type 取值：character_closeup / character_action / scene / screen_ui / fx_transition
（真人脸镜头禁用 agnes；场景与特效优先 agnes；屏幕 UI 用本地 Ken Burns）
写 "{{shot_type}}" 表示按分镜逐镜取镜头类型，引擎会照它给每一镜选模型（动漫线就这么用）"""

OUTPUT_CONTRACT = """只输出一个 JSON 对象，不要 markdown 代码块，不要解释文字：
{
  "reply": "给用户看的中文说明，说清你改了什么、为什么这么改，150 字以内",
  "proposals": [
    {"action":"workflow.node.patch","workflow":"工作流名","node_id":"节点id",
     "base_hash":"原样照抄上下文里给的 base_hash","patch":{"字段":新值},"reason":"一句话理由"},
    {"action":"workflow.node.add","workflow":"工作流名","base_hash":"...",
     "node":{"id":"新节点id","type":"image","depends_on":["上游"],"prompt":"..."},"reason":"..."},
    {"action":"workflow.node.remove","workflow":"工作流名","base_hash":"...",
     "node_id":"节点id","reason":"..."},
    {"action":"workflow.meta.patch","workflow":"工作流名","base_hash":"...",
     "patch":{"description":"...","version":"...","execution":{"max_retries":2}},"reason":"..."},
    {"action":"workflow.create","workflow":"新工作流名","base_hash":"",
     "doc":{"name":"新工作流名","nodes":{...}},"reason":"..."},
    {"action":"prompt.patch","key":"模板文件名不含扩展名","base_hash":"...",
     "patch":{"prompt":"整段新提示词","config":{"temperature":0.8}},"reason":"..."},
    {"action":"prompt.create","key":"新模板名","base_hash":"",
     "doc":{"name":"...","type":"llm","prompt":"...","config":{}},"reason":"..."}
  ]
}

规则：
1. 一次最多 12 条提案。改 prompt 用 prompt.patch（会整体覆盖 prompt 字段，所以必须给完整新正文）。
2. patch 里放 null 表示删除该字段。除 null 外不许出现你想不出值的占位符。
3. 用户只是在提问、或你要的改动无法用以上动作表达时，proposals 给空数组，把话写在 reply 里。
4. 涉及 foreach 节点时，改字段要落在循环节点本身，不要落在它的上游。
5. 改比例/时长/分辨率这类会连带影响下游的字段时，把受影响的下游节点一起改了，并在 reply 里说明。
6. base_hash 必须逐字照抄上下文里给的值；上下文没给就填空串。
7. 改 llm 模板正文时，引用上游节点字段一律写成 {{节点id.字段}}；
   裸 {{字段}} 只有工作流 input 里传进来的键、以及 foreach 当前项的字段才取得到。"""


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _wf_hash(name: str) -> str:
    try:
        path = workflow_store.path_for(name)
        return _sha(path.read_text(encoding="utf-8")) if path.exists() else ""
    except Exception:
        return ""


def _prompt_hash(key: str) -> str:
    try:
        path = prompt_store.path_for(key)
        return _sha(path.read_text(encoding="utf-8")) if path.exists() else ""
    except Exception:
        return ""


# ------------------------------------------------------------------ 上下文

def _collect_context(workflow: str, node_id: str, prompt_key: str) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"workflows": {}, "prompts": {}}

    names = [workflow] if workflow else workflow_store.list_workflows()
    for name in names[:3]:
        try:
            doc = workflow_store.read(name)
        except workflow_store.StoreError as exc:
            raise HTTPException(400, str(exc))
        text = json.dumps(doc, ensure_ascii=False, indent=1)
        ctx["workflows"][name] = {
            "doc": text[:MAX_DOC_BYTES],
            "truncated": len(text) > MAX_DOC_BYTES,
            "base_hash": _wf_hash(name),
        }
        # llm 节点引用的模板一起带上，否则助手看不见正文
        for cfg in (doc.get("nodes") or {}).values():
            if isinstance(cfg, dict) and cfg.get("prompt_template"):
                k = str(cfg["prompt_template"]).removesuffix(".yaml").strip()
                ctx["prompts"].setdefault(k, None)

    if prompt_key:
        ctx["prompts"][prompt_key] = None

    for k in list(ctx["prompts"]):
        try:
            doc = prompt_store.read(k)
        except prompt_store.StoreError:
            ctx["prompts"][k] = {"missing": True, "base_hash": ""}
            continue
        text = json.dumps(doc, ensure_ascii=False, indent=1)
        ctx["prompts"][k] = {
            "doc": text[:MAX_DOC_BYTES],
            "truncated": len(text) > MAX_DOC_BYTES,
            "base_hash": _prompt_hash(k),
        }
    return ctx


def _render_context(ctx: Dict[str, Any], focus_node: str) -> str:
    parts = [f"可用工作流列表：{workflow_store.list_workflows()}"]
    parts.append(f"可用提示词模板：{[t['key'] for t in prompt_store.list_templates()]}")
    for name, item in ctx["workflows"].items():
        head = f"当前工作流 {name}（base_hash={item['base_hash']}）"
        if focus_node:
            head += f"，用户当前选中的节点：{focus_node}"
        parts.append(f"{head}：\n{item['doc']}" + ("（已截断）" if item["truncated"] else ""))
    for key, item in ctx["prompts"].items():
        if item.get("missing"):
            parts.append(f"提示词模板 {key}：文件还不存在（新建走 prompt.create）")
            continue
        parts.append(f"提示词模板 {key}.yaml（base_hash={item['base_hash']}）：\n{item['doc']}")
    return "\n\n".join(parts)


# ------------------------------------------------------------------ 校验提案

_NAME_OK = re.compile(r"^[\w\u4e00-\u9fa5.\- ]{1,80}$")


def _check_names(proposal: Dict[str, Any]) -> None:
    for field in ("workflow", "node_id", "key"):
        val = proposal.get(field)
        if val is not None and (not isinstance(val, str) or not _NAME_OK.match(val)):
            raise ValueError(f"{field} 名称非法：{val!r}")


def _validate_proposal(p: Dict[str, Any]) -> Tuple[bool, str]:
    """结构 + 引用完整性校验。返回 (是否可用, 拒绝原因)。真正落盘时还要过一遍引擎校验。"""
    if not isinstance(p, dict):
        return False, "提案不是对象"
    action = p.get("action")
    if action not in ACTIONS:
        return False, f"未知动作：{action!r}"
    try:
        _check_names(p)
    except ValueError as exc:
        return False, str(exc)

    if not isinstance(p.get("reason"), str):
        p["reason"] = ""

    if action in ("workflow.node.patch", "workflow.node.remove"):
        wf, nid = p.get("workflow"), p.get("node_id")
        if not wf or not nid:
            return False, f"{action} 需要 workflow 和 node_id"
        try:
            doc = workflow_store.read(wf)
        except workflow_store.StoreError as exc:
            return False, str(exc)
        nodes = doc.get("nodes") or {}
        if nid not in nodes:
            return False, f"节点不存在：{wf}/{nid}（现有：{list(nodes)}）"
        if action.endswith("patch"):
            patch = p.get("patch")
            if not isinstance(patch, dict) or not patch:
                return False, "patch 必须是非空对象"
            if "type" in patch and str(patch["type"]).lower() not in workflow_store.NODE_TYPES:
                return False, f"不支持的节点类型：{patch['type']!r}"
            p["before"] = {k: nodes[nid].get(k) for k in patch}
        else:
            # 删除时下游的 depends_on 会自动去掉这一条（见 _apply_one），
            # 但要让用户看得见，否则画布上凭空少一条边很难查。
            p["also_rewire"] = [n for n, c in nodes.items()
                                if nid in ((c or {}).get("depends_on") or [])]
        p["base_hash"] = p.get("base_hash") or _wf_hash(wf)

    elif action == "workflow.node.add":
        wf = p.get("workflow")
        node = p.get("node")
        if not wf or not isinstance(node, dict):
            return False, "workflow.node.add 需要 workflow 和 node 对象"
        nid = node.get("id") or p.get("node_id")
        if not nid or not _NAME_OK.match(str(nid)):
            return False, "新节点缺少合法 id"
        node["id"] = str(nid)
        if str(node.get("type", "")).lower() not in workflow_store.NODE_TYPES:
            return False, f"新节点 type 非法：{node.get('type')!r}"
        try:
            doc = workflow_store.read(wf)
        except workflow_store.StoreError as exc:
            return False, str(exc)
        if nid in (doc.get("nodes") or {}):
            return False, f"节点 {nid} 已存在，请改用 workflow.node.patch"
        p["base_hash"] = p.get("base_hash") or _wf_hash(wf)

    elif action == "workflow.meta.patch":
        wf = p.get("workflow")
        patch = p.get("patch")
        if not wf or not isinstance(patch, dict) or not patch:
            return False, "workflow.meta.patch 需要 workflow 和非空 patch"
        bad = set(patch) & {"nodes", "name"}
        if bad:
            return False, f"meta.patch 不能改 {bad}，节点请用 workflow.node.*"
        p["base_hash"] = p.get("base_hash") or _wf_hash(wf)

    elif action == "workflow.create":
        wf, doc = p.get("workflow"), p.get("doc")
        if not wf or not isinstance(doc, dict):
            return False, "workflow.create 需要 workflow 和 doc"
        if workflow_store.path_for(wf).exists():
            return False, f"工作流 {wf} 已存在，请逐节点打补丁"
        doc.setdefault("name", wf)
        try:
            workflow_store.validate(wf, doc)
        except workflow_store.StoreError as exc:
            return False, f"新工作流不合法：{exc}"
        p["base_hash"] = ""

    elif action == "prompt.patch":
        key = p.get("key")
        patch = p.get("patch")
        if not key or not isinstance(patch, dict) or not patch:
            return False, "prompt.patch 需要 key 和非空 patch"
        try:
            old = prompt_store.read(key)
        except prompt_store.StoreError:
            return False, f"模板不存在：{key}（新建请走 prompt.create）"
        if "prompt" in patch and not isinstance(patch["prompt"], str):
            return False, "prompt 字段必须是字符串"
        merged = {**old, **{k: v for k, v in patch.items() if v is not None}}
        try:
            prompt_store.validate(key, merged)
        except prompt_store.StoreError as exc:
            return False, str(exc)
        p["before"] = {k: old.get(k) for k in patch}
        p["base_hash"] = p.get("base_hash") or _prompt_hash(key)

    elif action == "prompt.create":
        key, doc = p.get("key"), p.get("doc")
        if not key or not isinstance(doc, dict):
            return False, "prompt.create 需要 key 和 doc"
        if prompt_store.path_for(key).exists():
            return False, f"模板 {key} 已存在，请改用 prompt.patch"
        try:
            prompt_store.validate(key, doc)
        except prompt_store.StoreError as exc:
            return False, f"新模板不合法：{exc}"
        p["base_hash"] = ""

    return True, ""


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
    text = text.removesuffix("```").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("返回值不是 JSON 对象")
    return data


# ------------------------------------------------------------------ 接口

class CopilotRequest(BaseModel):
    message: str = Field(..., description="用户的自然语言诉求")
    workflow: str = ""
    node_id: str = ""
    prompt_key: str = ""
    history: List[Dict[str, str]] = Field(default_factory=list)


class ApplyRequest(BaseModel):
    proposals: List[Dict[str, Any]]


@router.post("/chat")
async def chat(req: CopilotRequest):
    if not req.message.strip():
        raise HTTPException(400, "message 不能为空")
    ctx = _collect_context(req.workflow.strip(), req.node_id.strip(), req.prompt_key.strip())

    system = (
        "你是 ai-drama-studio 的流水线编辑助手。用户在看一条 AI 视频流水线的画布，"
        "你的工作是把他的一句话诉求，翻译成对「工作流 JSON」和「提示词 yaml 模板」的最小修改提案。\n\n"
        f"{FIELD_REFERENCE}\n\n{OUTPUT_CONTRACT}\n\n"
        f"=== 现场上下文 ===\n{_render_context(ctx, req.node_id)}"
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    for h in (req.history or [])[-8:]:
        if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            messages.append({"role": h["role"], "content": h["content"][:4000]})
    messages.append({"role": "user", "content": req.message[:4000]})

    from ..llm.planner import chat_completion

    try:
        raw = await chat_completion(messages=messages, temperature=0.3,
                                    max_tokens=3500, response_format={"type": "json_object"})
        data = _parse_llm_json(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"AI 助手返回解析失败：{exc}")

    raw_proposals = data.get("proposals")
    if not isinstance(raw_proposals, list):
        raw_proposals = []
    proposals, rejected = [], []
    for p in raw_proposals[:MAX_PROPOSALS]:
        ok, why = _validate_proposal(p if isinstance(p, dict) else {})
        (proposals if ok else rejected).append(p if ok else {"raw": p, "reason": why})
    if len(raw_proposals) > MAX_PROPOSALS:
        rejected.append({"raw": "…", "reason": f"超过单轮上限 {MAX_PROPOSALS} 条，多余的已丢弃"})

    return {
        "reply": str(data.get("reply") or "").strip(),
        "proposals": proposals,
        "rejected": rejected,
        "context": {
            "workflow": req.workflow,
            "workflows": list(ctx["workflows"]),
            "prompts": list(ctx["prompts"]),
        },
    }


def _apply_one(p: Dict[str, Any]) -> None:
    """把一条已校验的提案落到内存文档上。"""
    action = p["action"]
    if action == "workflow.node.patch":
        doc = workflow_store.read(p["workflow"])
        node = doc["nodes"][p["node_id"]]
        for k, v in p["patch"].items():
            if v is None:
                node.pop(k, None)
            else:
                node[k] = v
        workflow_store.write(p["workflow"], doc)

    elif action == "workflow.node.add":
        doc = workflow_store.read(p["workflow"])
        node = dict(p["node"])
        nid = node.pop("id")
        doc["nodes"][nid] = node
        workflow_store.write(p["workflow"], doc)

    elif action == "workflow.node.remove":
        doc = workflow_store.read(p["workflow"])
        doc["nodes"].pop(p["node_id"])
        for cfg in doc["nodes"].values():
            deps = cfg.get("depends_on") or []
            if p["node_id"] in deps:
                cfg["depends_on"] = [d for d in deps if d != p["node_id"]]
        workflow_store.write(p["workflow"], doc)

    elif action == "workflow.meta.patch":
        doc = workflow_store.read(p["workflow"])
        for k, v in p["patch"].items():
            if v is None:
                doc.pop(k, None)
            else:
                doc[k] = v
        workflow_store.write(p["workflow"], doc)

    elif action == "workflow.create":
        workflow_store.write(p["workflow"], p["doc"], create=True)

    elif action == "prompt.patch":
        doc = prompt_store.read(p["key"])
        for k, v in p["patch"].items():
            if v is None:
                doc.pop(k, None)
            elif isinstance(v, dict) and isinstance(doc.get(k), dict):
                doc[k] = {**doc[k], **v}
            else:
                doc[k] = v
        prompt_store.write(p["key"], doc)

    elif action == "prompt.create":
        prompt_store.write(p["key"], p["doc"], create=True)


@router.post("/apply")
async def apply(req: ApplyRequest):
    """用户点「确认应用」后调用。逐条重校验（提案可能是旧的），并核对 base_hash。"""
    if not req.proposals:
        raise HTTPException(400, "没有要应用的提案")
    if len(req.proposals) > MAX_PROPOSALS:
        raise HTTPException(400, f"一次最多应用 {MAX_PROPOSALS} 条提案")

    # 同一批提案来自同一次提问，针对同一份文档时共用同一个「提案时指纹」：
    # 只有第一条比对磁盘现状，后面的接着本批已改完的状态往下走。
    origin: Dict[Tuple[str, str], str] = {}
    applied, failed = [], []

    for p in req.proposals:
        claimed = p.get("base_hash") or ""
        ok, why = _validate_proposal(p)
        if not ok:
            failed.append({"proposal": p, "reason": why})
            continue

        target = ("workflow", p.get("workflow", "")) if p["action"].startswith("workflow") \
            else ("prompt", p.get("key", ""))
        if target not in origin:
            origin[target] = "" if p["action"] == "workflow.create" else (
                _wf_hash(target[1]) if target[0] == "workflow" else _prompt_hash(target[1]))
        if claimed and claimed != origin[target]:
            failed.append({"proposal": p,
                           "reason": f"{target[1]} 在提案之后又被改过，请重新提问生成新提案"})
            continue

        try:
            _apply_one(p)
        except (workflow_store.StoreError, prompt_store.StoreError) as exc:
            failed.append({"proposal": p, "reason": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001
            failed.append({"proposal": p, "reason": f"写入失败：{exc}"})
            continue

        applied.append({"action": p["action"], "target": "/".join(target),
                        "reason": p.get("reason", "")})

    return {"applied": applied, "failed": failed,
            "ok": len(applied) == len(req.proposals)}
