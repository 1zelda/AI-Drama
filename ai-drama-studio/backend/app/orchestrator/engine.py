"""Workflow configuration and execution engine.

A workflow is a DAG of nodes described in JSON. Each node declares a ``type``:

=================  ==============================================================
``llm``            Render a YAML prompt template through a chat model.
``image``          Generate stills (ComfyUI locally, or an HTTP image API).
``video``          Generate a clip (ComfyUI / Seedance / Kling / Agnes).
``ffmpeg``         Concatenate upstream clips.
=================  ==============================================================

What this rewrite fixes / adds
------------------------------
* ``image`` is a real node type now. Previously only ``comfyui`` existed, so
  ``standard-drama.json``'s ``"type": "image"`` raised ``ValueError``.
* ``execution.retry`` and ``execution.timeout`` are honoured. They used to be
  dead config — written into the JSON and never read back.
* ``{{seed}}`` renders to the right type. ComfyUI rejects a string seed, so
  values are coerced to int/float where the workflow expects a number.
* ``foreach`` fans a node out over a list (one image per shot, one clip per
  storyboard row) and collects results, emitting per-item progress.
* Every step emits an event through ``on_event`` so the API can stream it (SSE).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..services.provider_picker import missing_hint, pick


# {{a.b.c}} 形式的「上游节点内部字段」引用
_DOT_REF = re.compile(r"\{\{([A-Za-z_]\w*(?:\.\w+)+)\}\}")


class NodeState:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.status = "pending"  # pending, running, completed, failed, waiting_approval
        self.output = None
        self.error = None
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.attempts = 0
        self.items_done = 0
        self.items_total = 0

    def to_dict(self, include_output: bool = False) -> dict:
        """落盘时要把 output 一起带上，否则重启后只能看到状态、拿不到产物。"""
        data = {
            "node_id": self.node_id,
            "status": self.status,
            "error": self.error,
            "attempts": self.attempts,
            "items_done": self.items_done,
            "items_total": self.items_total,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
        if include_output:
            data["output"] = self.output
        return data


class WorkflowConfig:
    """Loads and validates workflow configuration from JSON."""

    def __init__(self, config_path):
        self.config_path = Path(config_path)
        with open(self.config_path, encoding="utf-8") as f:
            self.config = json.load(f)
        self.name = self.config.get("name", self.config_path.stem)
        self.description = self.config.get("description", "")
        self.nodes = self.config.get("nodes", {})
        self.execution = self.config.get("execution", {})
        self._validate()

    def _validate(self):
        if "name" not in self.config:
            raise ValueError("Workflow missing required field: name")
        if not self.nodes:
            raise ValueError("Workflow has no nodes")
        for node_id, node in self.nodes.items():
            if "type" not in node:
                raise ValueError(f"Node {node_id} missing 'type'")
            node.setdefault("depends_on", [])

    def get_node(self, node_id: str) -> dict:
        return self.nodes.get(node_id)

    def get_execution_order(self) -> List[str]:
        """Topological sort; raises on cycles instead of silently dropping nodes."""
        visited: set = set()
        in_stack: set = set()
        order: List[str] = []

        def visit(node_id: str):
            if node_id in visited:
                return
            if node_id in in_stack:
                raise ValueError(f"工作流存在循环依赖，涉及节点：{node_id}")
            in_stack.add(node_id)
            for dep in self.nodes[node_id].get("depends_on", []):
                if dep not in self.nodes:
                    raise ValueError(f"节点 {node_id} 依赖了不存在的节点 {dep}")
                visit(dep)
            in_stack.discard(node_id)
            visited.add(node_id)
            order.append(node_id)

        for node_id in self.nodes:
            visit(node_id)
        return order

    def get_parallel_groups(self) -> List[List[str]]:
        """Group nodes whose dependencies are already satisfied."""
        completed: set = set()
        groups: List[List[str]] = []
        remaining = set(self.nodes.keys())
        while remaining:
            ready = [
                nid for nid in remaining
                if set(self.nodes[nid].get("depends_on", [])).issubset(completed)
            ]
            if not ready:
                break  # circular dependency
            groups.append(ready)
            completed.update(ready)
            remaining -= set(ready)
        return groups


class WorkflowOrchestrator:
    """DAG execution engine with retries, timeouts, fan-out and progress events."""

    def __init__(self, workflow_config: WorkflowConfig, approval_gate=None,
                 output_dir: str = "output", on_event: Optional[Callable[[dict], None]] = None,
                 store=None, resume: bool = False):
        self.config = workflow_config
        self.approval_gate = approval_gate
        self.output_dir = Path(output_dir)
        self.node_states: Dict[str, NodeState] = {}
        self.context: Dict[str, Any] = {}
        self._providers = {}
        self.on_event = on_event
        self.events: List[dict] = []
        self.run_id: Optional[str] = None
        # store: RunStore，负责把状态/产物增量写盘；resume: 复用同一 run_id 的
        # checkpoint，只跑上次没跑完的镜头（见 execute()）。
        self.store = store
        self.resume = resume

    def register_provider(self, name, provider):
        self._providers[name] = provider

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #

    def _emit(self, event_type: str, **payload):
        event = {"type": event_type, "ts": time.time(), "run_id": self.run_id, **payload}
        self.events.append(event)
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    async def execute(self, input_data: dict, run_id: Optional[str] = None) -> dict:
        self.run_id = run_id
        self.context.update(input_data or {})
        self.node_states = {nid: NodeState(nid) for nid in self.config.nodes}
        execution_order = self.config.get_execution_order()

        exec_cfg = self.config.execution or {}
        default_retries = int(exec_cfg.get("retry", 0) or 0)
        default_timeout = float(exec_cfg.get("timeout") or 0) or None
        default_concurrency = int(exec_cfg.get("concurrency") or 1)

        checkpoint = self._load_checkpoint()

        self._emit("run_start", workflow=self.config.name, nodes=execution_order,
                   resumed=bool(checkpoint))

        # 心跳：等供应商排队时几分钟没有任何事件是正常的，前端靠它区分
        # 「还在跑」和「后端已经死了」—— 否则长跑中途无声退出只能干等。
        heartbeat_task = None
        hb_interval = float(exec_cfg.get("heartbeat_interval") or 10)
        if hb_interval > 0:
            heartbeat_task = asyncio.create_task(self._heartbeat(hb_interval))

        try:
            for node_id in execution_order:
                node = self.config.nodes[node_id]
                state = self.node_states[node_id]

                # 被调用方关掉的节点（如一键成片页关掉配音）直接跳过，
                # 下游拿不到它的输出就自然降级，不需要改工作流结构。
                if node.get("disabled"):
                    state.status = "skipped"
                    state.started_at = state.completed_at = datetime.now()
                    self._emit("node_done", node_id=node_id, skipped=True)
                    continue

                state.started_at = datetime.now()
                state.status = "running"

                if self.approval_gate and node.get("approval_before"):
                    pending = self.approval_gate.get_pending(stage=node_id)
                    if pending:
                        state.status = "waiting_approval"
                        self._emit("node_waiting_approval", node_id=node_id, pending=pending)
                        continue

                self._emit("node_start", node_id=node_id, node_type=node.get("type"))

                deps_output: Dict[str, Any] = {}
                for dep in node.get("depends_on", []):
                    dep_state = self.node_states.get(dep)
                    if dep_state and dep_state.output is not None:
                        deps_output[dep] = dep_state.output

                retries = int(node.get("retry", default_retries) or 0)
                timeout = float(node.get("timeout") or default_timeout or 0) or None

                try:
                    items = self._resolve_foreach(node, deps_output)
                    state.items_total = len(items)

                    # 预分配结果列表：断点续跑时先把跑过的项回填，再只跑剩下的，
                    # 顺序必须和 items 严格对齐（下游按 index 取首帧）。
                    results: List[Any] = [None] * len(items)
                    todo = list(range(len(items)))

                    if self.store and self.resume and self.run_id:
                        saved = self.store.load_shots(self.run_id, node_id)
                        reused = 0
                        for index in range(len(items)):
                            payload = saved.get(index)
                            if payload is not None and self._checkpoint_usable(payload):
                                results[index] = payload
                                reused += 1
                        if reused:
                            todo = [i for i in range(len(items)) if results[i] is None]
                            self._emit("node_progress", node_id=node_id, value=0,
                                       status=f"断点续跑：复用已完成 {reused}/{len(items)} 项")

                    # 关键帧预览门：首次只生成前 N 项，人工确认后再 resume 跑剩下的。
                    # 批量生图是这条线最贵的一步，先出候选选优能省掉大量废片。
                    gate = int(node.get("preview_gate") or 0)
                    gated = False
                    if gate and not self.resume and len(todo) > gate:
                        todo = todo[:gate]
                        gated = True

                    # 首尾帧续接要读上一项产物，并发时顺序无法保证，自动退化成串行。
                    concurrency = int(node.get("concurrency") or default_concurrency or 1)
                    if node.get("tail_frame_from_prev") or node.get("prev_frame_refs"):
                        concurrency = 1

                    if concurrency > 1 and len(todo) > 1:
                        await self._run_pool(todo, items, deps_output, node_id, node, retries,
                                             timeout, results, state, concurrency)
                    else:
                        for index in todo:
                            merged = self._build_render_context(deps_output, items[index], index)
                            # 上一项产物：给「上一镜尾帧做下一镜参考」用（见 _execute_video）
                            merged["_prev"] = results[index - 1] if index > 0 else None
                            results[index] = await self._run_with_retry(
                                node_id, node, deps_output, merged, retries, timeout
                            )
                            state.items_done = sum(1 for r in results if r is not None)
                            if self.store and self.run_id:
                                self.store.save_shot(self.run_id, node_id, index, results[index])
                            self._emit("item_done", node_id=node_id, index=index, total=len(items),
                                       progress=int(state.items_done / max(len(items), 1) * 100))

                    if gated:
                        # 只跑了前 N 项：停在这里等人工确认，别带着半成品往下跑。
                        state.status = "waiting_approval"
                        state.output = [r for r in results if r is not None]
                        self._emit("node_waiting_approval", node_id=node_id, done=len(state.output),
                                   total=len(items), preview_gate=gate)
                    else:
                        # Single-item nodes return the bare result so downstream
                        # {{node_id.xxx}} lookups keep working exactly as before.
                        state.output = results[0] if len(results) == 1 else results
                        state.status = "completed"
                        self._emit("node_done", node_id=node_id, output=self._preview(state.output))
                except Exception as exc:
                    state.status = "failed"
                    state.error = f"{type(exc).__name__}: {exc}"
                    self._emit("node_error", node_id=node_id, error=state.error,
                               traceback=traceback.format_exc(limit=6))
                    # 状态落盘交给 finally，失败也要记下来，续跑才知道这节点跑崩过
                    if node.get("continue_on_error"):
                        continue
                    raise
                finally:
                    state.completed_at = datetime.now()
                    self._persist_node(node_id)

                if state.status == "waiting_approval":
                    self._emit("run_done", status="waiting_approval", node_id=node_id)
                    return self._finalize("waiting_approval")

            self._emit("run_done", status="completed")
            return self._finalize("completed")
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()

    async def _run_with_retry(self, node_id, node, deps, merged, retries: int,
                              timeout: Optional[float]) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(1, retries + 2):
            self.node_states[node_id].attempts = attempt
            try:
                coro = self._execute_node(node_id, node, deps, merged)
                return await (asyncio.wait_for(coro, timeout=timeout) if timeout else coro)
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"节点 {node_id} 超时（{timeout}s）")
            except Exception as exc:
                last_error = exc
                if _is_permanent(exc):
                    break  # missing API key / bad config: retrying cannot help
            if attempt <= retries:
                delay = min(2 ** (attempt - 1), 15)
                self._emit("node_retry", node_id=node_id, attempt=attempt,
                           delay=delay, error=str(last_error))
                await asyncio.sleep(delay)
        raise last_error  # type: ignore[misc]

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #

    async def _execute_node(self, node_id: str, config: dict, deps: dict, merged: dict) -> Any:
        node_type = (config.get("type") or "").lower()
        handlers = {
            "llm": self._execute_llm,
            "text": self._execute_llm,
            "image": self._execute_image,
            "comfyui_image": self._execute_image,
            "comfyui": self._execute_image,
            "video": self._execute_video,
            "comfyui_video": self._execute_video,
            "agnes_video": self._execute_agnes_video,
            "tts": self._execute_tts,
            "video_input": self._execute_video_input,
            "ffmpeg": self._execute_ffmpeg,
            "postprocess": self._execute_postprocess,
            "noop": self._execute_noop,
        }
        handler = handlers.get(node_type)
        if handler is None:
            raise ValueError(
                f"未知节点类型 {node_type!r}（节点 {node_id!r}）。支持: {sorted(handlers)}"
            )
        return await handler(node_id, config, deps, merged)

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def _build_render_context(self, deps: dict, item: Any, index: int) -> dict:
        """Merge workflow context + upstream outputs + the current foreach item."""
        merged: Dict[str, Any] = dict(self.context)
        merged.update(deps)
        merged["index"] = index
        if isinstance(item, dict):
            merged.update(item)
            merged.setdefault("item", item)
        elif item is not None:
            merged["item"] = item
        return merged

    def _resolve_foreach(self, node: dict, deps: dict) -> List[Any]:
        """Expand ``foreach: "storyboard.shots"`` into the items to iterate over.

        ``storyboard`` 是节点声明的 ``output`` 名，而 deps 的键是节点 id
        （``storyboard_generator``）。以前只在 deps 里查，结果永远查不到，
        foreach 退化成单元素、6 个镜头的分镜只生成 1 张图。这里补一层别名映射。
        """
        spec = node.get("foreach")
        if not spec:
            return [None]

        aliases: Dict[str, Any] = {}
        for node_id, node_cfg in self.config.nodes.items():
            alias = node_cfg.get("output")
            if alias and node_id in deps:
                aliases[str(alias)] = deps[node_id]

        value = self._lookup_path(spec, deps, aliases)
        if value is None:
            value = self._lookup_path(spec, aliases)
        if isinstance(value, list):
            return value or [None]
        if isinstance(value, dict):
            out = []
            for key, val in value.items():
                out.append({"key": key, **val} if isinstance(val, dict) else {"key": key, "value": val})
            return out
        return [value if value is not None else None]

    def _lookup_path(self, spec: str, deps: dict, *extra_roots) -> Any:
        """Resolve a dotted path, checking upstream outputs first, then context."""
        parts = str(spec).split(".")
        for root in ((deps,) + extra_roots + (self.context,)):
            current: Any = root
            ok = True
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                    current = current[int(part)]
                else:
                    ok = False
                    break
            if ok:
                return current
        return None

    def _render(self, value, merged: dict):
        """Substitute ``{{key}}`` placeholders, supporting dotted paths."""
        if not isinstance(value, str) or "{{" not in value:
            return value
        out = value
        # Longest keys first so {{shot.prompt}} is not shadowed by {{shot}}.
        for key in sorted(merged, key=len, reverse=True):
            out = out.replace("{{" + key + "}}", self._stringify(merged[key]))
        for key in sorted(merged, key=len, reverse=True):
            if "." not in key:
                continue
            head = key.split(".")[0]
            if head in merged:
                resolved = self._lookup_path_in(key, merged[head])
                if resolved is not None:
                    out = out.replace("{{" + key + "}}", self._stringify(resolved))
        # 上游字段引用：{{script_planner.title}} / {{storyboard.shots.0.subtitle}} 这类。
        # 以前只能引用扁平 key，取上游节点内部的字段必须自己在 JSON 里写死。
        out = _DOT_REF.sub(
            lambda m: (
                self._stringify(resolved)
                if (resolved := self._lookup_path(m.group(1), merged)) is not None
                else m.group(0)
            ),
            out,
        )
        return out

    @staticmethod
    def _lookup_path_in(dotted: str, root: Any) -> Any:
        current = root
        for part in dotted.split(".")[1:]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                return None
        return current

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _render_config(self, config: dict, merged: dict) -> dict:
        """Render every string leaf of a node config, skipping control keys."""
        skip = {"type", "depends_on", "output", "retry", "timeout", "foreach",
                "provider", "approval_before", "continue_on_error", "disabled"}
        out = {}
        for key, value in config.items():
            if key in skip:
                out[key] = value
            elif isinstance(value, str):
                out[key] = self._render(value, merged)
            elif isinstance(value, dict):
                out[key] = {k: self._render(v, merged) for k, v in value.items()}
            elif isinstance(value, list):
                out[key] = [self._render(v, merged) for v in value]
            else:
                out[key] = value
        return out

    # ------------------------------------------------------------------ #
    # Node handlers
    # ------------------------------------------------------------------ #

    async def _execute_noop(self, node_id, config, deps, merged) -> Any:
        return {"node_id": node_id, "result": "noop"}

    async def _execute_llm(self, node_id: str, config: dict, deps: dict, merged: dict) -> Any:
        import yaml

        prompt_template = config.get("prompt_template", "")
        if not prompt_template:
            return {"node_id": node_id, "result": "llm_placeholder"}

        template_path = (
            Path(__file__).parent.parent.parent / "config" / "prompts" / "scripts" / prompt_template
        )
        if not template_path.exists():
            raise FileNotFoundError(f"提示词模板不存在：{template_path}")
        with open(template_path, encoding="utf-8") as f:
            template = yaml.safe_load(f) or {}

        prompt = template.get("prompt", "")
        # 走统一的 _render：以前这里自己写了一遍扁平 key 替换，结果 {{节点id.字段}}
        # 这种上游引用在 llm 节点里永远不被解析 —— 画风圣经传给分镜的一整段规格
        # 会以原样的大括号进入提示词，模型只能自由发挥。
        prompt = self._render(prompt, merged)

        from ..llm.planner import chat_completion

        # 挂 images_from 就改走多模态通道：模型看不了视频文件，只能看几张图，
        # 所以「反推一条现成视频」（片段魔改线）依赖 video_input 抽出来的关键帧。
        watch = config.get("images_from")
        if watch:
            from ..llm.planner import vision_completion

            frames = self._collect_media(deps.get(watch), role="image")[:4]
            if not frames:
                raise ValueError(
                    f"节点 {node_id!r} 要「看图说话」，但上游 {watch!r} 没有可用图片产物。"
                    "检查 video_input 是否跑成功、ffmpeg 是否在 PATH 上。")
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"送入 {len(frames)} 张关键帧做反推")
            content = await vision_completion(
                prompt, frames, model=config.get("model"),
                max_tokens=self._as_int(config.get("max_tokens")) or 2500)
            if config.get("json_mode", True) is False:
                return {"node_id": node_id, "text": content}
            cleaned = (content or "").strip()
            for fence in ("```json", "```"):
                if cleaned.startswith(fence):
                    cleaned = cleaned[len(fence):]
            cleaned = cleaned.removesuffix("```").strip()
            return json.loads(cleaned)

        create_kwargs: Dict[str, Any] = {
            "messages": [{"role": "user", "content": prompt}],
        }
        if config.get("model"):
            create_kwargs["model"] = config["model"]
        json_mode = config.get("json_mode", True)
        if json_mode:
            create_kwargs["response_format"] = {"type": "json_object"}

        content = await chat_completion(**create_kwargs)
        if json_mode:
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                cleaned = content.strip()
                for fence in ("```json", "```"):
                    if cleaned.startswith(fence):
                        cleaned = cleaned[len(fence):]
                cleaned = cleaned.removesuffix("```").strip()
                return json.loads(cleaned)
        return {"node_id": node_id, "text": content}

    async def _execute_image(self, node_id: str, config: dict, deps: dict, merged: dict) -> Any:
        """Generate one still per foreach item, via ComfyUI or an HTTP API."""
        from ..providers.image_providers import PROVIDERS as IMAGE_PROVIDERS, get_image_provider
        from .shot_routing import check_banned, fill_motion_template, load_routing, route_provider, watermark_preset_for

        cfg = self._render_config(config, merged)
        provider_name = (cfg.get("provider") or "").lower() or None

        # 镜头路由：显式 provider 优先；否则按 shot_type 派发（只在本模态支持的范围内派）
        routed = route_provider(cfg.get("shot_type"), provider_name,
                                allowed=set(IMAGE_PROVIDERS) | {"comfyui_image"})
        # 兜底：路由给的 provider 没配 Key 时，自动顺延到一个真能用的通道
        picked = pick("image", routed["provider"])
        if not picked["provider"]:
            raise ValueError(missing_hint("生图"))
        if picked.get("fallback_from"):
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"生图通道 {picked['fallback_from']} 未配置，自动改用 {picked['provider']}")
        provider_name = picked["provider"]
        if banned := check_banned(cfg.get("shot_type"), provider_name or ""):
            raise ValueError(banned)

        if provider_name == "http":
            provider = get_image_provider("http")
        elif provider_name in (None, "comfyui", "comfyui_image"):
            provider = get_image_provider(provider_name or "comfyui", workflow_file=cfg.get(
                "workflow_file", "scene-generation.json"), server_url=cfg.get("comfy_url"),
                output_node=cfg.get("output_node"))
        else:
            provider = get_image_provider(provider_name)

        prompt = cfg.get("prompt") or self._resolve_prompt(cfg, deps, merged)
        if not prompt:
            raise ValueError(f"节点 {node_id!r} 缺少 prompt")
        # 防融化纪律：人物镜头提示词缺运动描述时自动套模板
        if cfg.get("shot_type") and "静止" not in prompt:
            motion = fill_motion_template(cfg.get("shot_type"), cfg.get("micro_action"))
            if motion and provider_name in ("zhipu", "modelscope", "siliconflow"):
                prompt = f"{prompt}。画面动态：{motion}"

        out_dir = self.output_dir / self.config.name / node_id
        out_dir.mkdir(parents=True, exist_ok=True)

        refs: List[str] = []
        init_ref: Optional[str] = None
        for key in ("reference_image", "reference_images"):
            value = cfg.get(key)
            if isinstance(value, str):
                refs.append(value)
            elif isinstance(value, list):
                refs.extend(str(v) for v in value)
        if cfg.get("images_from"):
            refs.extend(self._collect_media(deps.get(cfg["images_from"]), role="image"))
        # 逐镜配对：第 i 镜拿第 i 张图当"被改写的对象"。images_from 是把上游所有图一次
        # 性全挂上，魔改线要的是"这一镜对应源视频的哪一帧"，所以单独一条通道。
        if cfg.get("init_image_from"):
            pool = self._collect_media(deps.get(cfg["init_image_from"]), role="image")
            idx = merged.get("index")
            if pool and isinstance(idx, int) and 0 <= idx < len(pool):
                refs.append(pool[idx])
                init_ref = pool[idx]
            elif pool:
                self._emit("node_progress", node_id=node_id, value=0,
                           status=f"⚠️ 第 {idx} 镜没有配到的源帧（共 {len(pool)} 帧），"
                                  "这一镜只能按文字重画")
        # 跨镜角色一致性：补参考图 + 形象锚点（见 _apply_character_refs）
        if cfg.get("character_refs"):
            refs, prompt = self._apply_character_refs(cfg, deps, merged, node_id, refs, prompt)
        # 上一镜成品图做参考：连续镜头的色调/光影能接得上。默认关 ——
        # 参考图权重高时容易把构图也一起带过去，看起来像同一张图换动作。
        if cfg.get("prev_frame_refs"):
            for p in self._collect_media(merged.get("_prev"), role="image")[:1]:
                if p and p not in refs:
                    refs.append(p)
        refs = [r for r in refs if r and Path(r).exists()]

        async def _gen(prompt_text: str) -> dict:
            """一次生图 → 统一 payload。质检要重生成时复用同一条通道。"""
            result = await provider.generate(
                prompt=prompt_text,
                negative_prompt=cfg.get("negative_prompt"),
                width=int(cfg.get("width", 1024)),
                height=int(cfg.get("height", 1024)),
                seed=self._as_int(cfg.get("seed")),
                steps=self._as_int(cfg.get("steps")),
                cfg=self._as_float(cfg.get("cfg")),
                reference_images=refs or None,
                loras=cfg.get("loras"),
                output_dir=str(out_dir),
                filename=self._safe_name(cfg.get("filename") or f"{node_id}_{merged.get('index', 0):03d}"),
                on_progress=lambda v, m, n: self._emit(
                    "node_progress", node_id=node_id, value=v, max=m, comfy_node=n),
            )
            item = result.to_dict()
            item["node_id"] = node_id
            item["prompt"] = prompt_text
            item["provider"] = provider_name
            if init_ref:
                # 记住这一镜是照着哪张源帧改的：质检要拿它做"改前/改后"对比
                item["init_image"] = init_ref
            return item

        payload = await _gen(prompt)

        # 生图质检：VLM 打分，不及格就按它的改写建议重生成（见 _qc_loop）
        qc_cfg = cfg.get("qc") or {}
        if qc_cfg.get("enabled") and payload.get("paths"):
            payload = await self._qc_loop(node_id, qc_cfg, payload, prompt, _gen, merged)

        # 自动去水印：供应商有角标预设且开关开启时，对产物过一遍 LaMa
        routing_cfg = load_routing().get("global", {})
        if routing_cfg.get("auto_remove_watermark") and (wm := watermark_preset_for(provider_name or "", cfg.get("shot_type"))):
            import asyncio as _asyncio

            from ..providers.watermark import remove_watermark

            async def _clean(path: str) -> str:
                return await _asyncio.to_thread(
                    remove_watermark, path, provider=wm,
                    output_path=str(Path(path).with_name(Path(path).stem + "_clean" + Path(path).suffix)),
                )

            try:
                if payload.get("paths"):
                    cleaned: List[str] = []
                    for p in payload["paths"]:
                        c = await _clean(p)
                        # 去水印是按视频写的，对图片可能吐出一堆 H.264 数据却仍叫 .png。
                        # 校验不过就保留原图 —— 宁可留着角标，也不能给下游一张打不开的首帧。
                        cleaned.append(c if _readable_image(c) else p)
                    payload["paths"] = cleaned
                    payload["urls"] = list(payload["paths"])
            except Exception as exc:  # noqa: BLE001 - 去水印失败不阻断主流程
                self._emit("node_progress", node_id=node_id, value=0, status=f"去水印跳过: {exc}")

        # 定妆图回填角色档案库：这一镜之后所有镜头都能拿它当参考图（见 _register_character）
        if cfg.get("register_character"):
            self._register_character(node_id, cfg, merged, payload)
        return payload

    # ------------------------------------------------------------------ #
    # 角色一致性
    # ------------------------------------------------------------------ #

    def _character_profiles(self, cfg: dict, deps: dict) -> Dict[str, Dict[str, Any]]:
        """角色名 → {anchor, refs}，两个来源都是为了「同一角色每镜长一样」：

        1. 角色档案库（CharacterManager）：用户上传/定妆的参考图，效果最强；
        2. 上游角色设定节点（character_generator）：LLM 输出的形象描述。
           即使一张参考图都没有，也能保证跨镜的锚点文本逐字一致 —— 这正是
           以前只靠「分镜师自觉复制」最容易走样的地方。
        """
        profiles: Dict[str, Dict[str, Any]] = {}

        try:
            project = str(cfg.get("character_project")
                          or self.context.get("project_id") or "default")
            from ..providers.character_manager import CharacterManager

            storage = str(Path(__file__).parent.parent.parent / "data")
            cm = CharacterManager(project, storage)
            for char in cm.list_characters():
                name = str(char.name or "").strip()
                if not name:
                    continue
                slot = profiles.setdefault(name, {"anchor": "", "refs": []})
                if not slot["anchor"]:
                    slot["anchor"] = "，".join(
                        str(p) for p in (char.appearance, char.costume) if p
                    )
                slot["refs"] = [p for p in cm.get_reference_paths(char.id) if Path(p).exists()]
        except Exception:  # noqa: BLE001 - 档案库读不到就退化成只用上游设定
            pass

        for value in deps.values():
            for item in self._iter_characters(value):
                name = str(item.get("name") or "").strip()
                anchor = str(item.get("image_prompt") or item.get("appearance") or "").strip()
                if not name:
                    continue
                slot = profiles.setdefault(name, {"anchor": "", "refs": []})
                if anchor and not slot["anchor"]:
                    slot["anchor"] = anchor

        return {k: v for k, v in profiles.items() if v["anchor"] or v["refs"]}

    @classmethod
    def _iter_characters(cls, node: Any):
        """从上游输出里挖出角色条目（有 name + 形象描述的 dict）。"""
        if isinstance(node, list):
            for item in node:
                yield from cls._iter_characters(item)
            return
        if isinstance(node, dict):
            if node.get("name") and (node.get("image_prompt") or node.get("appearance")):
                yield node
            for key in ("characters", "roles", "cast"):
                if isinstance(node.get(key), list):
                    yield from cls._iter_characters(node[key])

    def _shot_character_names(self, merged: dict, known: List[str]) -> List[str]:
        """这一镜出现了哪些角色。

        优先读分镜里显式写的 characters；没写就在镜头文本里做名字匹配 ——
        老分镜（还没有这个字段）也能直接受益，不用重跑生成。
        """
        raw = merged.get("characters") or merged.get("character_names") or merged.get("character")
        names: List[str] = []
        if isinstance(raw, str):
            names = [x.strip() for x in re.split(r"[,，、/|]+", raw) if x.strip()]
        elif isinstance(raw, list):
            names = [str(x).strip() for x in raw if str(x).strip()]
        elif isinstance(raw, dict):
            names = [str(raw.get("name") or "").strip()]

        matched: List[str] = []
        for name in names:
            if not name:
                continue
            if name in known:
                matched.append(name)
                continue
            hit = next((k for k in known if k and (k in name or name in k)), None)
            if hit:
                matched.append(hit)
        if matched:
            return matched

        item = merged.get("item")
        text = " ".join(str(merged.get(k) or "") for k in
                        ("image_prompt", "description", "subtitle", "video_prompt", "prompt"))
        if isinstance(item, dict):
            text += " " + json.dumps(item, ensure_ascii=False)
        elif item:
            text += " " + str(item)
        return [k for k in known if k and k in text]

    def _apply_character_refs(self, cfg: dict, deps: dict, merged: dict, node_id: str,
                              refs: List[str], prompt: str):
        """把角色参考图塞进 reference_images，并把形象锚点补进 prompt。

        只加不覆盖：工作流里手写的 reference_image 优先级更高。
        """
        profiles = self._character_profiles(cfg, deps)
        if not profiles:
            return refs, prompt
        names = self._shot_character_names(merged, list(profiles))
        if not names:
            return refs, prompt

        max_refs = self._as_int(cfg.get("character_refs_max")) or 3
        added: List[str] = []
        anchors: List[str] = []
        for name in names[:max_refs]:
            slot = profiles[name]
            for path in slot["refs"]:
                if path not in refs and path not in added:
                    added.append(path)
            anchor = slot["anchor"]
            if anchor and anchor not in prompt and anchor not in anchors:
                anchors.append(anchor)

        if added:
            refs = list(refs) + added
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"角色参考图 {len(added)} 张（{'、'.join(names)}）")
        if anchors:
            prompt = f"{prompt}。角色形象锚点（必须与之一致）：" + "；".join(anchors)
        return refs, prompt

    def _register_character(self, node_id: str, cfg: dict, merged: dict, payload: dict) -> None:
        """把这张定妆图登记进角色档案库，变成后续所有镜头的参考图。

        以前定妆图只是「给人看一眼挑一张」的产物，生成完就断链了：档案库空的，
        下一镜仍只能靠文字锚点。回填之后同一角色的脸才能真正跨镜锁住。

        只回填本节点自己产出过的路径 —— 重跑会换图，但用户上传的参考图一张都不动。
        """
        paths = [str(p) for p in (payload.get("paths") or []) if p and Path(str(p)).exists()]
        if not paths:
            return
        name = str(merged.get(cfg.get("character_name_field") or "name") or "").strip()
        if not name:
            self._emit("node_progress", node_id=node_id, value=0,
                       status="⚠️ 定妆图未登记：分镜条目里没有角色名字段")
            return

        try:
            from ..providers.character_manager import CharacterManager

            project = str(cfg.get("character_project")
                          or self.context.get("project_id") or "default")
            storage = str(Path(__file__).parent.parent.parent / "data")
            cm = CharacterManager(project, storage)
            char = next((c for c in cm.list_characters() if str(c.name).strip() == name), None)
            if char is None:
                char = cm.add_character(
                    name=name,
                    role=str(merged.get("role") or "supporting"),
                    description=str(merged.get("description") or ""),
                    appearance=str(merged.get("image_prompt") or merged.get("appearance") or ""),
                    costume=str(merged.get("costume") or ""),
                    personality=str(merged.get("personality") or ""),
                )

            mine = str(self.output_dir / self.config.name / node_id)
            cap = self._as_int(cfg.get("register_character_max")) or 3
            keep = [p for p in cm.get_reference_paths(char.id)
                    if not str(p).startswith(mine)]
            for p in paths[:cap]:
                if p not in keep:
                    keep.append(p)
            cm.update_character(char.id, reference_images=keep)
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"定妆图已登记为「{name}」的参考图（共 {len(keep)} 张）")
        except Exception as exc:  # noqa: BLE001 - 档案库写不进去不该让定妆图白跑
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"定妆图登记跳过：{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ #
    # 生图质检（VLM 打分 + 低分改写重生成）
    # ------------------------------------------------------------------ #

    async def _qc_review(self, node_id: str, qc: dict, payload: dict, merged: dict) -> dict:
        """让视觉模型给这张图挑毛病。

        返回 {score, issues, rewrite_prompt}。模型没配/调用失败一律返回 {}：
        质检是加分项，绝不能因为它把生图本身搞挂。
        """
        paths = [p for p in (payload.get("paths") or []) if p and Path(p).exists()]
        if not paths and payload.get("local_path") and Path(payload["local_path"]).exists():
            paths = [payload["local_path"]]
        if not paths:
            return {}

        prompt = qc.get("prompt") or ""
        template_path = (
            Path(__file__).parent.parent.parent / "config" / "prompts" / "scripts"
            / (qc.get("prompt_template") or "qc_vision.yaml")
        )
        if template_path.exists():
            import yaml

            with open(template_path, encoding="utf-8") as f:
                prompt = (yaml.safe_load(f) or {}).get("prompt", "") or prompt
        if not prompt:
            return {}

        ctx = {k: v for k, v in merged.items() if isinstance(v, (str, int, float))}
        ctx["image_prompt"] = payload.get("prompt") or ctx.get("image_prompt") or ""
        for key in sorted(ctx, key=len, reverse=True):
            prompt = prompt.replace("{{" + key + "}}", str(ctx[key]))

        # 魔改线要「改前 / 改后」一起看：qc 里写 compare_source: true，就把这一镜
        # 照着改的那张源帧一起送进去，模板按「第 1 张=重绘结果，第 2 张=原片帧」判构图。
        imgs = paths[:1]
        source_frame = payload.get("init_image")
        if qc.get("compare_source") and source_frame and Path(str(source_frame)).exists():
            imgs = imgs + [str(source_frame)]

        try:
            from ..llm.planner import vision_completion

            raw = await vision_completion(prompt, imgs, model=qc.get("model"))
        except Exception as exc:  # noqa: BLE001 - 质检不可用就当没这道门
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"质检不可用（{type(exc).__name__}），本次跳过")
            return {}

        text = (raw or "").strip()
        for fence in ("```json", "```"):
            if text.startswith(fence):
                text = text[len(fence):]
        text = text.removesuffix("```").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        try:
            score = float(data.get("score"))
        except (TypeError, ValueError):
            return {}
        return {"score": score, "issues": data.get("issues") or [],
                "rewrite_prompt": str(data.get("rewrite_prompt") or "").strip()}

    async def _qc_loop(self, node_id: str, qc: dict, payload: dict, prompt: str,
                       gen, merged: dict) -> dict:
        """打分 → 不及格就按 VLM 的改写建议重生成，最多 max_attempts 次。

        全都没过阈值时保留得分最高的那张 —— 重写也有可能越写越差。
        """
        threshold = float(qc.get("threshold", 6))
        max_attempts = max(1, int(qc.get("max_attempts", 2)))
        best, best_score = payload, None

        for attempt in range(1, max_attempts + 1):
            review = await self._qc_review(node_id, qc, payload, merged)
            score = review.get("score")
            if score is None:
                return payload
            payload["qc"] = {"score": score, "issues": review.get("issues") or [],
                             "attempt": attempt, "threshold": threshold}
            if best_score is None or score > best_score:
                best, best_score = payload, score
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"质检 {score:g} 分（阈值 {threshold:g}）")
            if score >= threshold or attempt >= max_attempts:
                break
            hint = review.get("rewrite_prompt")
            if not hint:
                break
            self._emit("node_progress", node_id=node_id, value=0, status="质检未过，按建议重生成")
            payload = await gen(f"{prompt}。修正要求：{hint}")

        return best if best is not payload else payload

    async def _tail_frame_of(self, node_id: str, prev: Any) -> Optional[str]:
        """抽上一镜视频的最后一帧，给下一镜做视觉连续参考。"""
        if not prev:
            return None
        path = prev.get("local_path") if isinstance(prev, dict) else None
        if not path:
            media = self._collect_media(prev, role="video")
            path = media[-1] if media else None
        if not path or not Path(str(path)).exists():
            return None
        try:
            from ..services.postprod import extract_tail_frame

            dest_dir = self.output_dir / self.config.name / f"{node_id}_tailframes"
            dest_dir.mkdir(parents=True, exist_ok=True)
            return await extract_tail_frame(
                str(path), str(dest_dir / f"{Path(str(path)).stem}_tail.png"))
        except Exception as exc:  # noqa: BLE001 - 抽帧失败不该让这一镜跑不起来
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"尾帧抽取跳过：{type(exc).__name__}")
            return None

    async def _execute_video(self, node_id: str, config: dict, deps: dict, merged: dict) -> Any:
        """Generate one clip per foreach item; provider chosen by ``config.provider`` or shot_type routing."""
        from ..providers.video_providers import PROVIDERS as VIDEO_PROVIDERS, get_provider
        from .shot_routing import check_banned, fill_motion_template, load_routing, route_provider, watermark_preset_for

        cfg = self._render_config(config, merged)
        provider_name = (cfg.get("provider") or "").lower() or None

        # 镜头路由：显式 provider 优先；否则按 shot_type 派发（只在本模态支持的范围内派）
        routed = route_provider(cfg.get("shot_type"), provider_name,
                                allowed=set(VIDEO_PROVIDERS) | {"comfyui_video"})
        picked = pick("video", routed["provider"])
        if not picked["provider"]:
            raise ValueError(missing_hint("生视频"))
        if picked.get("fallback_from"):
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"生视频通道 {picked['fallback_from']} 未配置，自动改用 {picked['provider']}")
        provider_name = picked["provider"]
        if banned := check_banned(cfg.get("shot_type"), provider_name or ""):
            raise ValueError(banned)
        provider_kwargs: Dict[str, Any] = {}
        if provider_name == "comfyui":
            # 视频侧以前不转 workflow_file，节点写了本地预设也只会跑默认 I2V 图；
            # 口型同步（InfiniteTalk 一类）必须能指定自己的 ComfyUI 预设。
            provider_kwargs = {"workflow_file": cfg.get("workflow_file") or "image-to-video.json",
                               "server_url": cfg.get("comfy_url"),
                               "output_node": cfg.get("output_node")}
        provider = get_provider(provider_name, api_key=cfg.get("api_key"), **provider_kwargs)

        prompt = cfg.get("prompt") or self._resolve_prompt(cfg, deps, merged)
        if not prompt:
            raise ValueError(f"节点 {node_id!r} 缺少 prompt")
        # 防融化纪律：人物镜头缺运动描述时自动套「一片段一动词」模板
        if cfg.get("shot_type") and "静止" not in prompt:
            motion = fill_motion_template(cfg.get("shot_type"), cfg.get("micro_action"))
            if motion:
                prompt = f"{prompt}。画面动态：{motion}"

        first_frame = cfg.get("first_frame")
        if not first_frame and cfg.get("first_frame_from"):
            media = self._collect_media(deps.get(cfg["first_frame_from"]), role="image")
            # 按 foreach 下标取对应首帧。以前固定取 media[0]，多镜头时
            # 每一镜都用同一张首帧，成片看起来像静态图配不同动作。
            idx = merged.get("index")
            if isinstance(idx, int) and 0 <= idx < len(media):
                first_frame = media[idx]
            else:
                first_frame = media[0] if media else None
        last_frame = cfg.get("last_frame")
        if not last_frame and cfg.get("last_frame_from"):
            media = self._collect_media(deps.get(cfg["last_frame_from"]), role="image")
            idx = merged.get("index")
            # 尾帧必须严格按下标配对：下标越界时退回 media[0] 会把第一镜的尾帧
            # 接到第 N 镜上，画面会突然跳一下。宁可不给尾帧，退成普通图生视频。
            last_frame = media[idx] if isinstance(idx, int) and 0 <= idx < len(media) else None
        # 远程 URL 也是合法首帧（供应商能自己取），只有本地路径才需要存在性校验。
        # 这里以前无条件 exists() 检查，把云端产物的 http 地址全判成无效丢掉，
        # 结果是每一镜都退化成文生视频（横屏默认尺寸）。
        if first_frame and not str(first_frame).startswith(("http://", "https://")) \
                and not Path(first_frame).exists():
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"⚠️ 首帧不存在已丢弃：{first_frame}")
            first_frame = None
        if first_frame:
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"首帧 {Path(str(first_frame)).name}")
        if last_frame and not str(last_frame).startswith(("http://", "https://")) \
                and not Path(last_frame).exists():
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"⚠️ 尾帧不存在已丢弃：{last_frame}")
            last_frame = None
        if last_frame:
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"尾帧 {Path(str(last_frame)).name}")

        if provider.needs_public_assets and (first_frame or last_frame):
            from ..providers.asset_host import get_asset_host

            host = get_asset_host()
            if not host.configured:
                raise RuntimeError(
                    f"provider {provider.name} 需要公网可访问的图片，但未配置 PUBLIC_ASSET_BASE_URL。"
                    "改用 provider: comfyui 或 seedance 可以直接吃本地文件。"
                )
            first_frame = host.publish(first_frame, subdir=node_id) if first_frame else None
            last_frame = host.publish(last_frame, subdir=node_id) if last_frame else None

        # 首尾帧续接：把上一镜的尾帧作为参考图带进来。
        # 本镜首帧仍然是分镜图（构图由分镜决定），尾帧只负责延续上一镜的人物
        # 状态/光影/色调 —— 直接拿它当首帧会把构图也锁成上一镜的，镜头就死了。
        continuity_refs: List[str] = []
        if cfg.get("tail_frame_from_prev"):
            tail = await self._tail_frame_of(node_id, merged.get("_prev"))
            if tail:
                continuity_refs.append(tail)
                self._emit("node_progress", node_id=node_id, value=0,
                           status=f"续接上一镜尾帧 {Path(tail).name}")
        if provider.needs_public_assets and continuity_refs:
            from ..providers.asset_host import get_asset_host

            host = get_asset_host()
            if not host.configured:
                self._emit("node_progress", node_id=node_id, value=0,
                           status="⚠️ 未配置 PUBLIC_ASSET_BASE_URL，尾帧续接跳过")
                continuity_refs = []
            else:
                continuity_refs = [host.publish(p, subdir=node_id) for p in continuity_refs]

        out_dir = self.output_dir / self.config.name / node_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # 口型同步：把这一镜的配音喂给模型。目前只有 comfyui 的 InfiniteTalk 类预设吃它，
        # 云端供应商一律忽略（不报错），所以配了也不会把流程搞挂。
        audio = cfg.get("audio")
        if not audio and cfg.get("audio_from"):
            clips = self._collect_media(deps.get(cfg["audio_from"]), role="audio")
            idx = merged.get("index")
            audio = clips[idx] if isinstance(idx, int) and 0 <= idx < len(clips) else None
        if audio and not str(audio).startswith(("http://", "https://")) \
                and not Path(str(audio)).exists():
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"⚠️ 配音音频不存在，本镜不做口型同步：{audio}")
            audio = None
        if audio:
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"口型同步音频 {Path(str(audio)).name}")

        # 整段改风格（魔改线）：把上游 video_input 的源视频（或它切出来的一段）交给
        # 支持视频编辑的通道，比如百炼 wan2.7-videoedit、本地 ComfyUI 的 VACE 预设。
        # 按下标配对，和首帧同一套逻辑：一条源视频切成 N 段时就是一镜改一段。
        source_video = cfg.get("source_video")
        if not source_video and cfg.get("source_video_from"):
            pool = self._collect_media(deps.get(cfg["source_video_from"]), role="video")
            idx = merged.get("index")
            source_video = (pool[idx] if isinstance(idx, int) and 0 <= idx < len(pool)
                            else (pool[0] if pool else None))
        if source_video and not str(source_video).startswith(("http://", "https://")):
            # 本地文件只对 ComfyUI 有意义（它和后端在同一台机器上，供应商自己会上传）；
            # 云端读不到我们的磁盘，必须先发成公网 URL。
            if provider_name != "comfyui":
                from ..providers.asset_host import get_asset_host

                host = get_asset_host()
                if host.configured:
                    source_video = host.publish(source_video, subdir=node_id)
                else:
                    self._emit("node_progress", node_id=node_id, value=0,
                               status="⚠️ 源视频是本机路径且未配置 PUBLIC_ASSET_BASE_URL，"
                                      "云端通道取不到（改用 provider: comfyui 走本地）")
            if not str(source_video).startswith(("http://", "https://")):
                self._emit("node_progress", node_id=node_id, value=0,
                           status=f"源视频 {Path(str(source_video)).name}")
        if source_video:
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"改风格源视频 {Path(str(source_video)).name}")

        result = await provider.generate(
            prompt=prompt,
            reference_image=first_frame,
            duration=float(cfg.get("seconds", cfg.get("duration", 5))),
            mode=cfg.get("mode") or ("first_last_frame" if (first_frame and last_frame)
                                     else ("first_frame" if first_frame else "text")),
            last_frame=last_frame,
            images=(list(cfg.get("images") or []) + continuity_refs) or None,
            negative_prompt=cfg.get("negative_prompt"),
            width=self._as_int(cfg.get("width")),
            height=self._as_int(cfg.get("height")),
            fps=self._as_int(cfg.get("fps")),
            resolution=cfg.get("resolution", "720p"),
            ratio=cfg.get("ratio"),
            seed=self._as_int(cfg.get("seed")),
            size=cfg.get("size", "720P"),
            aspect_ratio=cfg.get("aspect_ratio", "16:9"),
            camera_fixed=bool(cfg.get("camera_fixed", False)),
            generate_audio=bool(cfg.get("generate_audio", False)),
            audio=audio,
            source_video=source_video,
            output_dir=str(out_dir),
            filename=self._safe_name(cfg.get("filename") or f"{node_id}_{merged.get('index', 0):03d}"),
            on_progress=lambda p, s: self._emit("node_progress", node_id=node_id, value=p, status=s),
        )
        payload = result.to_dict()
        payload["node_id"] = node_id
        payload["prompt"] = prompt
        payload["provider"] = provider_name
        if payload.get("used_fallback"):
            # 图生视频被拒、退回了文生视频：产物是模型默认横屏，和竖屏分镜图对不上。
            # 必须让用户在进度里看见，否则只会觉得「成片怎么有模糊边」。
            self._emit("node_progress", node_id=node_id, value=0,
                       status="⚠️ 本镜退回文生视频，画面不跟随分镜图")

        # 自动去水印：视频产物同样过 LaMa（流式重编码，CPU 几十秒）
        routing_cfg = load_routing().get("global", {})
        if routing_cfg.get("auto_remove_watermark") and result.local_path and \
                (wm := watermark_preset_for(provider_name or "", cfg.get("shot_type"))):
            import asyncio as _asyncio

            from ..providers.watermark import remove_watermark

            try:
                cleaned = await _asyncio.to_thread(
                    remove_watermark, result.local_path, provider=wm,
                    output_path=str(Path(result.local_path).with_name(
                        Path(result.local_path).stem + "_clean" + Path(result.local_path).suffix)),
                )
                payload["local_path"] = cleaned
                if payload.get("url"):
                    payload["url"] = payload["url"]
            except Exception as exc:  # noqa: BLE001
                self._emit("node_progress", node_id=node_id, value=0, status=f"去水印跳过: {exc}")
        return payload

    async def _execute_agnes_video(self, node_id: str, config: dict, deps: dict, merged: dict) -> Any:
        """Agnes Video 2.5 path, kept for backwards compatibility."""
        from ..providers.agnes import get_agnes_client
        from ..providers.asset_host import get_asset_host

        cfg = self._render_config(config, merged)
        prompt = cfg.get("prompt") or self._resolve_prompt(cfg, deps, merged)
        if not prompt:
            raise ValueError(f"节点 {node_id!r} 缺少 prompt")

        host = get_asset_host()

        def publish(value, _role: str = "image"):
            if not value:
                return None
            text = str(value)
            if text.startswith(("http://", "https://")):
                return text
            return host.publish(text, subdir=node_id)

        def first_from(dep_id: str):
            urls = self._collect_media(deps.get(dep_id), role="image")
            return urls[0] if urls else None

        first_frame = publish(
            cfg.get("first_frame")
            or (first_from(cfg["first_frame_from"]) if cfg.get("first_frame_from") else None)
        )
        last_frame = publish(
            cfg.get("last_frame")
            or (first_from(cfg["last_frame_from"]) if cfg.get("last_frame_from") else None)
        )
        images: List[str] = []
        for dep_id in cfg.get("images_from") or []:
            images.extend(self._collect_media(deps.get(dep_id), role="image"))
        images.extend(cfg.get("images") or [])
        images = [publish(u) for u in images if u]

        if (first_frame or last_frame or images) and not host.configured:
            raise RuntimeError(
                "首帧/尾帧/参考图需要公网 URL，但未配置 PUBLIC_ASSET_BASE_URL。"
                "改用 provider: seedance（支持 base64）或 comfyui 可避免此限制。"
            )

        client = get_agnes_client(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"),
                                  model=cfg.get("model"))
        out_dir = self.output_dir / self.config.name / node_id
        out_dir.mkdir(parents=True, exist_ok=True)

        result = await client.generate(
            prompt=prompt, model=cfg.get("model"), mode=cfg.get("mode"),
            seconds=cfg.get("seconds", 5), size=cfg.get("size", "720P"),
            aspect_ratio=cfg.get("aspect_ratio", "16:9"), seed=self._as_int(cfg.get("seed")),
            first_frame=first_frame, last_frame=last_frame, images=images or None,
            audios=cfg.get("audios"), videos=cfg.get("videos"),
            output_dir=str(out_dir), max_wait=cfg.get("max_wait", 900),
            on_progress=lambda p, s: self._emit("node_progress", node_id=node_id, value=p, status=s),
        )
        payload = result.to_dict()
        payload["node_id"] = node_id
        payload["prompt"] = prompt
        return payload

    async def _execute_tts(self, node_id: str, config: dict, deps: dict, merged: dict) -> Any:
        """一句台词/旁白 → 一个音频文件（按说话人分音色）。

        edge-tts 免费且无需部署，作为默认；GPT-SoVITS 走音色克隆（需先在配音页建音色）。
        时长也顺手探出来，后期合成用它决定镜头长度，保证音画对齐。

        多人对话戏最怕「三个人一个嗓子」，所以这里按说话人查音色：
        见 _resolve_speaker / _voice_for_speaker。没配任何音色字段时
        行为与以前完全一致（整段用 cfg.voice 一个音色）。
        """
        from ..providers import audio_providers as ap

        cfg = self._render_config(config, merged)
        text = (
            cfg.get("text")
            or self._resolve_prompt(cfg, deps, merged)
            or self._first_text(merged, cfg.get("text_field"))
        ).strip()
        if not text:
            raise ValueError(f"节点 {node_id!r} 缺少要朗读的文本")

        table = self._voice_table(cfg, deps)
        speaker, text = self._resolve_speaker(cfg, merged, text, table)
        spec = self._voice_for_speaker(cfg, speaker, table)
        engine = str(spec.get("engine") or cfg.get("engine") or "edge").lower()
        out_dir = self.output_dir / self.config.name / node_id
        out_dir.mkdir(parents=True, exist_ok=True)
        name = self._safe_name(cfg.get("filename") or f"{node_id}_{merged.get('index', 0):03d}")

        if engine == "gptsovits":
            voices = {v["name"]: v for v in ap.list_voices()}
            profile = voices.get(str(spec.get("voice_name") or cfg.get("voice_name") or ""))
            if not profile:
                raise ValueError(
                    f"音色 {spec.get('voice_name') or cfg.get('voice_name')!r} 不在音色库，"
                    "先在「配音」页添加，或改用 engine: edge"
                )
            content = await ap.tts_gptsovits(
                text, ref_audio_path=profile["ref_audio"],
                prompt_text=profile.get("prompt_text", ""),
                prompt_lang=profile.get("prompt_lang", "zh"),
            )
            out = out_dir / f"{name}.wav"
            used = profile["name"]
        else:
            content = await ap.tts_edge(
                text,
                voice=str(spec.get("voice") or cfg.get("voice", "zh-CN-YunxiNeural")),
                rate=str(spec.get("rate") or cfg.get("rate", "+0%")),
                pitch=str(spec.get("pitch") or cfg.get("pitch", "+0Hz")),
            )
            out = out_dir / f"{name}.mp3"
            used = str(spec.get("voice") or cfg.get("voice"))

        if speaker:
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"{speaker} → {used}")
        out.write_bytes(content)
        duration = None
        try:
            from ..services.postprod import probe
            duration = (await probe(str(out)))["duration"]
        except Exception:  # noqa: BLE001 - 探不到时长不阻断，后期会按画面时长兜底
            pass
        return {
            "node_id": node_id, "text": text, "local_path": str(out),
            "paths": [str(out)], "duration": duration, "engine": engine,
            "speaker": speaker, "voice": used,
        }

    # 台词归属：分镜里可能显式写 "speaker": "林晚"，也可能把 "林晚：台词" 揉在一句里
    SPEAKER_FIELDS = ("speaker", "character", "role", "who", "说话人")

    def _voice_table(self, cfg: dict, deps: dict) -> Dict[str, Dict[str, Any]]:
        """角色名 → 音色配置。两个来源，手写的 voice_map 覆盖上游自动识别的：

        1. ``voices_from``：角色设定节点里每个角色的 ``voice`` 字段（LLM 选角时填）；
        2. ``voice_map``：工作流节点里直接写死的 {角色名: 音色}，改配置即可覆盖。

        音色值可以是字符串（edge-tts 音色 id），也可以是对象
        ``{"engine": "gptsovits", "voice_name": "林晚克隆"}`` —— 后者用于克隆音色。
        """
        table: Dict[str, Dict[str, Any]] = {}
        src = cfg.get("voices_from")
        for dep_id in ([src] if isinstance(src, str) else list(src or [])):
            for item in self._iter_characters(deps.get(dep_id)):
                name = str(item.get("name") or "").strip()
                spec = item.get("voice_spec") or item.get("voice")
                if not name or not spec:
                    continue
                table[name] = {"voice": spec} if isinstance(spec, str) else dict(spec)
        raw_map = cfg.get("voice_map")
        if isinstance(raw_map, dict):
            for name, spec in raw_map.items():
                if str(name).strip() and spec:
                    table[str(name).strip()] = {"voice": spec} if isinstance(spec, str) else dict(spec)
        return table

    def _resolve_speaker(self, cfg: dict, merged: dict, text: str,
                         table: Dict[str, Dict[str, Any]]) -> tuple[str, str]:
        """这句话是谁说的，以及剥掉角色前缀后的纯台词。"""
        field = cfg.get("speaker_field")
        for key in ((field,) if isinstance(field, str) else self.SPEAKER_FIELDS):
            value = merged.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), text
            if isinstance(value, dict):
                name = str(value.get("name") or "").strip()
                if name:
                    return name, text

        # 「角色名：台词」这种写法只有在名字确实是表里的角色时才拆。
        # 无脑拆会把「注意：」这类旁白开头误伤成角色名。
        m = re.match(r"^\s*([^：:，。！？\n]{1,8})[：:]\s*(.+)$", text, re.S)
        if m and m.group(1).strip() in table:
            return m.group(1).strip(), m.group(2).strip()
        return "", text

    def _voice_for_speaker(self, cfg: dict, speaker: str,
                           table: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """按说话人取音色；查不到就退到 voice_pool 稳定分配，再退到节点默认音色。"""
        spec = table.get(speaker)
        if not spec and speaker:
            hit = next((k for k in table if k and (k in speaker or speaker in k)), None)
            spec = table.get(hit) if hit else None
        if spec:
            return spec
        pool = cfg.get("voice_pool")
        if speaker and isinstance(pool, list) and len(pool) > 1:
            # 稳定散列（不能用内置 hash()，它每个进程都不同）：
            # 同一角色每次重跑都拿同一个音色，不会这一集是男声下一集变女声。
            idx = sum((i + 1) * ord(ch) for i, ch in enumerate(speaker)) % len(pool)
            return {"voice": str(pool[idx])}
        return {}

    async def _execute_postprocess(self, node_id: str, config: dict, deps: dict, merged: dict) -> Any:
        """把零散片段合成一条成片：归一化 → 烧字幕 → 贴配音 → 转场 → 垫 BGM。

        这是「像样的视频产出」的最后一公里。AI 接口给的片段分辨率、帧率、
        有无音轨都不一致，直接拼会跳变、没声音。这里统一规格并按配音时长
        重定时长，保证音画对齐。
        """
        from ..services.postprod import BuildOptions, Segment, build_video

        cfg = self._render_config(config, merged)

        sources: List[str] = []
        for dep_id in cfg.get("from") or list(deps):
            sources.extend(self._collect_media(deps.get(dep_id), role="video"))
        sources = [s for s in _dedupe(sources) if s and os.path.isfile(s)]
        if not sources:
            return {"node_id": node_id, "result": "postprocess_skipped_no_inputs", "paths": []}

        if limit := self._as_int(cfg.get("limit")):
            sources = sources[:limit]

        voices: List[str] = []
        for dep_id in cfg.get("voices_from") or []:
            voices.extend(self._collect_media(deps.get(dep_id), role="audio"))
        subs: List[str] = []
        for dep_id in cfg.get("subtitles_from") or []:
            subs.extend(self._collect_texts(deps.get(dep_id), cfg.get("subtitle_field")))

        segments = [
            Segment(
                video=src,
                voice=voices[i] if i < len(voices) else None,
                subtitle=subs[i] if i < len(subs) else "",
            )
            for i, src in enumerate(sources)
        ]

        dest_dir = self.output_dir / self.config.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / cfg.get("filename", "final.mp4")

        bgm = cfg.get("bgm") or None
        from_source_audio = False
        if not bgm and cfg.get("bgm_from"):
            # 魔改线：原片音轨垫进成片。台词的节奏、环境声、语气都留在画面上，
            # 换掉的只有世界观 —— 这是"魔改"和"重做一条"最直观的区别。
            pool = self._collect_media(deps.get(cfg["bgm_from"]), role="audio")
            bgm = pool[0] if pool else None
            from_source_audio = bool(bgm)
        if bgm and not Path(bgm).exists():
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"BGM 文件不存在，已跳过：{bgm}")
            bgm = None

        logo = cfg.get("logo") or None
        if not logo and cfg.get("logo_asset_id"):
            # 角标走资产库：页面上上传一次，之后所有成片复用同一张
            try:
                from ..api.assets import _load_index

                asset = _load_index().get(str(cfg["logo_asset_id"])) or {}
                logo = asset.get("path") or None
            except Exception:  # noqa: BLE001 - 资产库读不到就当没配角标
                logo = None
        if logo and not Path(str(logo)).exists():
            self._emit("node_progress", node_id=node_id, value=0,
                       status=f"角标文件不存在，已跳过：{logo}")
            logo = None

        # 片头/片尾文案支持从上游取（如 {{title}}），没配就不做
        title_card = cfg.get("title_card") or None
        end_card = cfg.get("end_card") or None

        opts = BuildOptions(
            width=int(cfg.get("width", 1080)),
            height=int(cfg.get("height", 1920)),
            fps=int(cfg.get("fps", 30)),
            fit=str(cfg.get("fit", "contain")),
            burn_subtitle=bool(cfg.get("burn_subtitle", True)),
            font=cfg.get("font") or None,
            font_size=self._as_int(cfg.get("font_size")),
            crossfade=float(cfg.get("crossfade", 0.3)),
            crf=int(cfg.get("crf", 20)),
            preset=str(cfg.get("preset", "veryfast")),
            bgm=bgm,
            bgm_gain_db=float(cfg.get("bgm_gain_db", -20)),
            # 原片音轨默认不循环（成片比源视频长时不会听见台词重播）；
            # 自己指定的 BGM 仍然铺满。显式写 bgm_loop 覆盖两者。
            bgm_loop=bool(cfg.get("bgm_loop", not from_source_audio)),
            voice_gain_db=float(cfg.get("voice_gain_db", 2)),
            title_card=title_card,
            title_card_sub=cfg.get("title_card_sub") or None,
            end_card=end_card,
            end_card_sub=cfg.get("end_card_sub") or None,
            card_seconds=float(cfg.get("card_seconds", 2.5)),
            logo=logo,
            logo_pos=str(cfg.get("logo_pos", "top-right")),
            logo_scale=float(cfg.get("logo_scale", 0.14)),
            logo_opacity=float(cfg.get("logo_opacity", 0.85)),
            trim_badge=bool(cfg.get("trim_badge", False)),
        )

        info = await build_video(
            segments, str(dest), opts,
            on_progress=lambda p, m: self._emit(
                "node_progress", node_id=node_id, value=p, status=m),
        )
        info["node_id"] = node_id
        info["paths"] = [info["path"]]
        info["sources"] = sources
        return info

    async def _execute_video_input(self, node_id: str, config: dict, deps: dict,
                                   merged: dict) -> Any:
        """把一条现成的视频读进流水线：探测参数 + 抽关键帧（+ 可选抽音轨/切片）。

        魔改线的入口。它和别的节点相反 —— 不生产内容，只是把外部素材变成
        下游能引用的东西：帧给多模态模型「看」，视频给编辑模型当参考，音轨垫成片。

        ``source`` 认四种写法：素材库 asset id、``/media/assets/xxx`` 链接、
        绝对路径、相对 backend 的路径。http(s) 直链先不支持（要落地才能 ffprobe，
        与其在这里塞一个没人验证过的下载器，不如让你走上传那条已经能用的路）。
        """
        from ..services.postprod import (extract_audio, extract_frames, ffmpeg_ready,
                                         cut_clip, probe)

        cfg = self._render_config(config, merged)
        raw = str(cfg.get("source") or self.context.get("source_video") or "").strip()
        if not raw:
            raise ValueError(
                "没有给源视频。在 /make 页面上传一段视频，或在运行输入里写 "
                "source_video=<素材库id 或 本地路径>。")

        src = self._resolve_source_media(raw, node_id)
        ok, why = ffmpeg_ready()
        if not ok:
            raise ValueError(why)

        info = await probe(str(src))
        out_dir = self.output_dir / self.config.name / node_id
        out_dir.mkdir(parents=True, exist_ok=True)

        payload: Dict[str, Any] = {
            "node_id": node_id, "result": "video_input",
            "local_path": str(src), "videos": [str(src)],
            "source_input": raw,
            "duration": info.get("duration"), "width": info.get("width"),
            "height": info.get("height"), "fps": info.get("fps"),
            "has_audio": bool(info.get("has_audio")),
            "size_mb": round(src.stat().st_size / 1048576, 2),
        }
        self._emit("node_progress", node_id=node_id, value=0,
                   status=f"源视频 {info.get('duration')}s "
                          f"{info.get('width')}x{info.get('height')}")

        want = self._as_int(cfg.get("frames")) or 4
        times = cfg.get("frame_times")
        frames = await extract_frames(
            str(src), str(out_dir / "frames"), count=want,
            max_side=self._as_int(cfg.get("max_side")),
            at=[float(t) for t in times] if isinstance(times, list) else None)
        payload["images"] = [f["path"] for f in frames]
        payload["frames"] = payload["images"]
        payload["frame_times"] = [f["time"] for f in frames]
        if not frames:
            self._emit("node_progress", node_id=node_id, value=0,
                       status="⚠️ 一帧都没抽出来，下游的反推会没有依据")

        if cfg.get("extract_audio"):
            audio = await extract_audio(str(src), str(out_dir / "source_audio.m4a"))
            if audio:
                payload["audios"] = [audio]
            else:
                self._emit("node_progress", node_id=node_id, value=0,
                           status="源视频没有可用音轨，成片将只用配音")

        # 片段级魔改：只改这几十秒。切片给下游当参考视频，也保证单段不超长。
        clip_len = self._as_int(cfg.get("clip_seconds"))
        if clip_len and info.get("duration"):
            segments = []
            total = float(info["duration"])
            start = 0.0
            n = 0
            while start < total - 0.05:
                end = min(start + clip_len, total)
                path = await cut_clip(str(src), str(out_dir / f"clip_{n + 1:02d}.mp4"),
                                      start, end)
                if path:
                    segments.append({"path": path, "start": round(start, 3),
                                     "end": round(end, 3), "seconds": round(end - start, 3)})
                    n += 1
                start = end
            if segments:
                payload["segments"] = segments
                payload["clips"] = [s["path"] for s in segments]
        return payload

    def _resolve_source_media(self, raw: str, node_id: str) -> Path:
        """把 ``source`` 那行字符串变成一个真实存在的本地文件路径。

        支持的写法刻意和素材库/前端保持一致：asset id、``/media/assets/`` 链接、
        绝对路径、相对 backend 的路径。
        """
        if raw.startswith(("http://", "https://")):
            raise ValueError(
                f"源视频暂不支持直链（{raw[:60]}…）：要先落到本机才能抽帧。"
                "请在 /make 页面上传，或先 POST /api/assets/upload 再把返回的 id 填进 source_video。")

        backend = Path(__file__).parent.parent.parent
        asset_dir = backend / "data" / "assets"
        candidates: List[Path] = []

        if raw.startswith("/media/assets/"):
            candidates.append(asset_dir / raw.rsplit("/", 1)[-1])
        # 直接读素材库索引文件，而不是 import api.assets：那个模块 import 时就要
        # python-multipart（表单依赖），引擎不该被 web 层的依赖绑住。
        try:
            entry = json.loads((asset_dir / "index.json").read_text(encoding="utf-8")).get(raw)
            if entry and entry.get("path"):
                candidates.append(Path(str(entry["path"])))
        except (OSError, json.JSONDecodeError, AttributeError):
            pass  # 索引坏了或没上传过，后面还有直接给路径这条路
        p = Path(raw)
        candidates += [p if p.is_absolute() else backend / p, asset_dir / p.name]

        for cand in candidates:
            if cand.is_file():
                return cand
        raise ValueError(
            f"找不到源视频：{raw}\n试过：{[str(c) for c in candidates]}。"
            "素材库里上传的文件请直接填上传接口返回的 id。")

    async def _execute_ffmpeg(self, node_id: str, config: dict, deps: dict, merged: dict) -> Any:
        """Concatenate upstream clips into one cut."""
        cfg = self._render_config(config, merged)
        sources: List[str] = []
        for dep_id in cfg.get("from") or list(deps):
            sources.extend(self._collect_media(deps.get(dep_id), role="video"))
        sources = [s for s in _dedupe(sources) if s and os.path.isfile(s)]

        if not sources:
            return {"node_id": node_id, "result": "ffmpeg_skipped_no_inputs", "paths": []}

        dest_dir = self.output_dir / self.config.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / cfg.get("filename", "final.mp4")

        list_file = dest_dir / f"{node_id}_concat.txt"
        list_file.write_text(
            "".join(f"file '{Path(s).as_posix()}'\n" for s in sources), encoding="utf-8"
        )

        # `-c copy` only works when every clip shares a codec; re-encode otherwise.
        args = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
        if cfg.get("reencode", False):
            args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "aac"]
        else:
            args += ["-c", "copy"]
        args.append(str(dest))

        # 不能用 PIPE：Windows 上 asyncio 会开读线程按系统 gbk 解码子进程输出，
        # 遇到非 ASCII（中文路径/中文报错）直接抛 UnicodeDecodeError，而且是在
        # 后台线程里抛，能把整个 uvicorn 进程带崩。和 postprod 一样落临时文件更稳。
        import tempfile

        with tempfile.TemporaryFile(mode="w+b") as errf:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=errf
            )
            await proc.communicate()
            if proc.returncode != 0:
                errf.seek(0)
                detail = errf.read().decode("utf-8", "replace")[-500:]
                raise RuntimeError(
                    f"ffmpeg concat failed (code {proc.returncode}): {detail}"
                )
        return {"node_id": node_id, "paths": [str(dest)], "sources": sources}

    # ------------------------------------------------------------------ #
    # 心跳 / 并发 / 收尾
    # ------------------------------------------------------------------ #

    async def _heartbeat(self, interval: float) -> None:
        """定期发一个 heartbeat 事件。

        长跑时几分钟没有进度是常态（供应商排队），前端没法靠「有没有新事件」
        判断后端是不是已经死了。有了心跳，静默超过阈值就能明确提示，
        而不是让用户对着一个不动的进度条干等。
        """
        try:
            while True:
                await asyncio.sleep(interval)
                done = sum(1 for s in self.node_states.values() if s.status == "completed")
                self._emit("heartbeat", nodes_done=done,
                           running=sum(1 for s in self.node_states.values() if s.status == "running"))
        except asyncio.CancelledError:
            return

    async def _run_pool(self, todo, items, deps_output, node_id, node, retries, timeout,
                        results, state, concurrency) -> None:
        """并发跑 foreach 的多项（生图/配音这类可并行的慢调用）。

        结果仍按 index 写回，顺序和串行完全一致；任何一项失败都抛出第一个异常，
        由外层的节点错误处理统一兜底。
        """
        sem = asyncio.Semaphore(max(1, concurrency))

        async def worker(index: int) -> None:
            async with sem:
                merged = self._build_render_context(deps_output, items[index], index)
                merged["_prev"] = results[index - 1] if index > 0 else None
                results[index] = await self._run_with_retry(
                    node_id, node, deps_output, merged, retries, timeout
                )
                state.items_done = sum(1 for r in results if r is not None)
                if self.store and self.run_id:
                    self.store.save_shot(self.run_id, node_id, index, results[index])
                self._emit("item_done", node_id=node_id, index=index, total=len(items),
                           progress=int(state.items_done / max(len(items), 1) * 100))

        outcomes = await asyncio.gather(*[worker(i) for i in todo], return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome

    def _finalize(self, status: str) -> dict:
        """收尾：汇总输出并落盘。写盘失败不能反过来把 run 判成失败。"""
        result = self._collect_output()
        if self.store and self.run_id:
            try:
                self.store.finish(self.run_id, status, result=result)
            except Exception:  # noqa: BLE001
                pass
        return result

    # ------------------------------------------------------------------ #
    # persistence / resume
    # ------------------------------------------------------------------ #

    def _load_checkpoint(self) -> dict:
        """本次是续跑时，读出上一轮的 run 记录。

        只有当工作流名一致时才复用 —— 拿 A 工作流的产物去补 B 工作流，
        下游节点拿到的字段名对不上，反而更难排查。
        """
        if not (self.store and self.resume and self.run_id):
            return {}
        try:
            meta = self.store.load(self.run_id) or {}
        except Exception:  # noqa: BLE001 - 读不到就当全新跑
            return {}
        if not meta:
            return {}
        if meta.get("workflow") and meta["workflow"] != self.config.name:
            self._emit("node_progress", node_id="", value=0,
                       status=f"checkpoint 属于工作流 {meta['workflow']}，本次不复用")
            return {}
        return meta

    def _persist_node(self, node_id: str) -> None:
        """节点一完成/一失败就写盘，进程中途死掉也能从上一个节点接着跑。"""
        if not (self.store and self.run_id):
            return
        state = self.node_states.get(node_id)
        if state is None:
            return
        try:
            self.store.save_node(self.run_id, node_id, state.to_dict(include_output=True))
        except Exception:  # noqa: BLE001 - 落盘失败不阻断主流程
            pass

    @staticmethod
    def _checkpoint_usable(payload: Any) -> bool:
        """checkpoint 里的产物文件还在才算数。

        用户在两轮之间点过「清理中间产物」，文件已经没了却仍复用 checkpoint，
        下游会拿到一堆不存在的路径 —— 宁可重跑这一镜。
        """
        if not isinstance(payload, dict):
            return payload is not None
        paths = payload.get("paths")
        if not isinstance(paths, list):
            paths = [payload["local_path"]] if payload.get("local_path") else []
        local = [p for p in paths
                 if isinstance(p, str) and p and not p.startswith(("http://", "https://"))]
        if not local:
            return True  # 纯文本产物（剧本/分镜）没有文件可校验
        return all(Path(p).exists() for p in local)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _as_int(value):
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_float(value):
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_name(value) -> str:
        return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(value))

    def _resolve_prompt(self, config: dict, deps: dict, merged: dict) -> str:
        """Find a prompt in config, an upstream dep, or the workflow context."""
        if config.get("prompt"):
            return config["prompt"]
        dep_id = config.get("prompt_from")
        if dep_id:
            out = deps.get(dep_id)
            if isinstance(out, str):
                return out
            if isinstance(out, dict):
                for key in ("prompt", "text", "video_prompt", "description"):
                    if out.get(key):
                        return str(out[key])
        return str(self.context.get("prompt") or merged.get("prompt") or "")

    @staticmethod
    def _collect_media(dep_output, role: str = "image") -> List[str]:
        """Extract media references from an upstream node's output."""
        if not dep_output:
            return []
        if isinstance(dep_output, str):
            return [dep_output]
        if isinstance(dep_output, list):
            out: List[str] = []
            for item in dep_output:
                out.extend(WorkflowOrchestrator._collect_media(item, role))
            return out
        if isinstance(dep_output, dict):
            # 优先级：本地列表 → 本地单值 → 远程地址。
            # 云端生图会同时给出 paths（已下载到本地）和 urls（远端地址）；远程优先的话
            # 下游拿到 http 链接，被 Path().exists() 判成「不存在」就整条丢掉，
            # 生视频会悄悄退化成文生视频（横屏默认尺寸），画面和分镜图完全对不上。
            list_keys = {
                "video": ("paths", "clips", "videos"),
                "audio": ("paths", "audios"),
            }.get(role, ("paths", "images", "frames"))

            for key in list_keys:
                value = dep_output.get(key)
                if isinstance(value, list) and value:
                    return [str(v) for v in value if v]

            # 本地单值（视频产物的 local_path）同样优先于远程地址
            single = dep_output.get("local_path") or dep_output.get("path")
            if single:
                return [str(single)]

            urls = dep_output.get("urls")
            if isinstance(urls, list) and urls:
                return [str(v) for v in urls if v]
            if dep_output.get("url"):
                return [str(dep_output["url"])]
            return []
        return []

    # 台词/字幕文本在分镜里的常见字段名，按顺序取第一个非空的
    TEXT_FIELDS = ("subtitle", "line", "narration", "dialogue", "text", "description")

    @classmethod
    def _collect_texts(cls, dep_output, field: Optional[str] = None) -> List[str]:
        """从一个上游节点的输出里按顺序抽出文本（分镜台词 / TTS 文本）。"""
        out: List[str] = []

        def walk(node: Any):
            if node is None:
                return
            if isinstance(node, str):
                if node.strip():
                    out.append(node.strip())
                return
            if isinstance(node, list):
                for item in node:
                    walk(item)
                return
            if isinstance(node, dict):
                for key in ((field,) if field else ()) + cls.TEXT_FIELDS:
                    value = node.get(key)
                    if isinstance(value, str) and value.strip():
                        out.append(value.strip())
                        return
                # 本层没有文本（例如 {"shots": [...]}）→ 继续往列表里找
                for value in node.values():
                    if isinstance(value, (list, dict)):
                        walk(value)
                return

        walk(dep_output)
        return out

    @classmethod
    def _first_text(cls, merged: dict, field: Optional[str] = None) -> str:
        for key in ((field,) if field else ()) + cls.TEXT_FIELDS:
            value = merged.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def _preview(self, output) -> Any:
        """Trim bulky outputs before pushing them over SSE."""
        if isinstance(output, list):
            return [self._preview(o) for o in output[:20]]
        if isinstance(output, dict):
            return {k: (v if not isinstance(v, (dict, list)) else "...")
                    for k, v in list(output.items())[:12]}
        return output

    def _collect_output(self) -> dict:
        return {nid: st.output for nid, st in self.node_states.items() if st.output is not None}

    def get_status(self) -> dict:
        return {nid: st.to_dict() for nid, st in self.node_states.items()}


def _readable_image(path: Optional[str]) -> bool:
    """文件确实是一张能被 PIL 打开的图片。用于校验去水印/后处理的产物。"""
    if not path:
        return False
    try:
        from PIL import Image

        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def _dedupe(items: List[str]) -> List[str]:
    seen: set = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _is_permanent(exc: Exception) -> bool:
    """Errors that fail identically on retry — don't burn attempts on them."""
    text = f"{type(exc).__name__}: {exc}".lower()
    permanent = (
        "not configured", "valueerror", "filenotfound", "unknown video provider",
        "unknown image provider", "permissionerror", "invalid api key",
    )
    return any(marker in text for marker in permanent)
