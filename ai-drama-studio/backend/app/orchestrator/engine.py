"""Workflow configuration and execution engine."""
from __future__ import annotations
import json
import asyncio
from typing import Dict, Any, List
from datetime import datetime
from pathlib import Path


class NodeState:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.status = "pending"  # pending, running, completed, failed, skipped
        self.output = None
        self.error = None
        self.started_at = None
        self.completed_at = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class WorkflowConfig:
    """Loads and validates workflow configuration from JSON."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        with open(config_path, encoding="utf-8") as f:
            self.config = json.load(f)
        self.name = self.config.get("name", config_path.stem)
        self.description = self.config.get("description", "")
        self.nodes = self.config.get("nodes", {})
        self.execution = self.config.get("execution", {})
        self._validate()

    def _validate(self):
        required = ["name", "nodes"]
        for field in required:
            if field not in self.config:
                raise ValueError(f"Workflow missing required field: {field}")
        for node_id, node in self.nodes.items():
            if "type" not in node:
                raise ValueError(f"Node {node_id} missing 'type'")
            if "depends_on" not in node:
                node["depends_on"] = []

    def get_node(self, node_id: str) -> dict:
        return self.nodes.get(node_id)

    def get_execution_order(self) -> List[str]:
        """Topological sort of nodes."""
        visited = set()
        order = []

        def visit(node_id):
            if node_id in visited:
                return
            visited.add(node_id)
            for dep in self.nodes[node_id].get("depends_on", []):
                visit(dep)
            order.append(node_id)

        for node_id in self.nodes:
            visit(node_id)
        return order

    def get_parallel_groups(self) -> List[List[str]]:
        """Group nodes that can run in parallel."""
        completed = set()
        groups = []
        remaining = set(self.nodes.keys())

        while remaining:
            # Find nodes whose dependencies are all satisfied
            ready = []
            for nid in remaining:
                deps = set(self.nodes[nid].get("depends_on", []))
                if deps.issubset(completed):
                    ready.append(nid)
            if not ready:
                break  # circular dependency
            groups.append(ready)
            completed.update(ready)
            remaining -= set(ready)

        return groups


class WorkflowOrchestrator:
    """DAG-based workflow execution engine."""

    def __init__(self, workflow_config: WorkflowConfig, approval_gate=None, output_dir: str = "output"):
        self.config = workflow_config
        self.approval_gate = approval_gate
        self.output_dir = Path(output_dir)
        self.node_states: Dict[str, NodeState] = {}
        self.context: Dict[str, Any] = {}
        self._providers = {}

    def register_provider(self, name, provider):
        self._providers[name] = provider

    async def execute(self, input_data: dict) -> dict:
        """Execute workflow with approval gates."""
        self.context.update(input_data)
        self.node_states = {nid: NodeState(nid) for nid in self.config.nodes}
        execution_order = self.config.get_execution_order()

        for node_id in execution_order:
            node = self.config.nodes[node_id]
            self.node_states[node_id].status = "running"
            self.node_states[node_id].started_at = datetime.now()

            try:
                # Gather dependency outputs
                deps_output = {}
                for dep in node.get("depends_on", []):
                    if dep in self.node_states and self.node_states[dep].output:
                        deps_output[dep] = self.node_states[dep].output

                # Check approval gate before executing
                if self.approval_gate and node.get("approval_before"):
                    pending = self.approval_gate.get_pending(stage=node_id)
                    if pending:
                        self.node_states[node_id].status = "waiting_approval"
                        continue

                result = await self._execute_node(node_id, node, deps_output)
                self.node_states[node_id].output = result
                self.node_states[node_id].status = "completed"
            except Exception as e:
                self.node_states[node_id].status = "failed"
                self.node_states[node_id].error = str(e)
                raise
            finally:
                self.node_states[node_id].completed_at = datetime.now()

        return self._collect_output()

    async def _execute_node(self, node_id: str, node_config: dict, deps: dict) -> Any:
        node_type = node_config.get("type")
        if node_type == "llm":
            return await self._execute_llm(node_id, node_config, deps)
        elif node_type in ("comfyui", "image"):
            return await self._execute_comfyui(node_id, node_config, deps)
        elif node_type == "video":
            return await self._execute_video(node_id, node_config, deps)
        elif node_type == "ffmpeg":
            return await self._execute_ffmpeg(node_id, node_config, deps)
        else:
            raise ValueError(f"Unknown node type: {node_type}")

    async def _execute_llm(self, node_id: str, config: dict, deps: dict) -> Any:
        from ..llm.planner import _get_client, _load_settings
        prompt_template = config.get("prompt_template", "")
        if not prompt_template:
            return {"node_id": node_id, "deps": deps, "result": "llm_placeholder"}
        # Load YAML template
        import yaml
        template_path = Path(__file__).parent.parent.parent / "config" / "prompts" / "scripts" / prompt_template
        with open(template_path, encoding="utf-8") as f:
            template = yaml.safe_load(f)
        prompt = template["prompt"]
        for k, v in deps.items():
            prompt = prompt.replace(f"{{{{{k}}}}}", json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
        # substitute the workflow's input context (title, description, ...)
        for k, v in self.context.items():
            prompt = prompt.replace(f"{{{{{k}}}}}", str(v))
        client = _get_client()
        response = await client.chat.completions.create(
            model=config.get("model") or _load_settings()["model"],
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)

    async def _execute_comfyui(self, node_id: str, config: dict, deps: dict) -> Any:
        from ..providers.comfyui import ComfyUIClient
        from ..services import generation
        workflow_path = Path(__file__).parent.parent.parent / "config" / "comfyui_workflows" / config.get("workflow_file", "scene-generation.json")
        if not workflow_path.exists():
            raise FileNotFoundError(f"Workflow file not found: {workflow_path.name}")
        client = ComfyUIClient()
        if not client.is_available():
            # Offline degradation: render labelled placeholders so the DAG still completes
            out_dir = Path(self.output_dir) / self.config.name / node_id
            out_dir.mkdir(parents=True, exist_ok=True)
            rendered = []
            for i in range(int(config.get("batch_size", 1) or 1)):
                target = out_dir / f"{node_id}_{i:03d}.png"
                if await generation.render_placeholder_image(target, f"{node_id} {i}"):
                    rendered.append(str(target))
            if not rendered:
                raise RuntimeError("ComfyUI 不可用且 ffmpeg 占位图渲染失败")
            return {"node_id": node_id, "paths": rendered, "engine": "placeholder"}
        workflow = client.load_workflow(workflow_path)
        # Apply patches from deps
        patches = {}
        for dep_id, dep_data in deps.items():
            if isinstance(dep_data, dict):
                patches[dep_id] = dep_data
        if patches:
            workflow = ComfyUIClient.patch_workflow(workflow, patches)
        output_node = config.get("output_node", "9")
        output_dir = Path(self.output_dir) / self.config.name / "comfyui"
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = client.generate(workflow, output_node, output_dir / node_id)
        return {"node_id": node_id, "paths": [str(p) for p in paths], "engine": "comfyui"}

    async def _execute_video(self, node_id: str, config: dict, deps: dict) -> Any:
        from ..providers.video_providers import get_provider
        provider_name = config.get("provider", "kling")
        prompt = ""
        for dep_data in deps.values():
            if isinstance(dep_data, dict):
                prompt = dep_data.get("summary") or dep_data.get("synopsis") or prompt
        try:
            provider = get_provider(provider_name)
            result = await provider.generate(prompt=prompt)
            url = result.url if hasattr(result, "url") else str(result)
            if url:
                return {"node_id": node_id, "result": url, "engine": provider_name}
        except Exception:
            pass
        # Offline degradation: placeholder clip
        from ..services import generation
        out_dir = Path(self.output_dir) / self.config.name
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{node_id}.mp4"
        if await generation.render_placeholder_clip(target, 3):
            return {"node_id": node_id, "result": str(target), "engine": "placeholder"}
        raise RuntimeError(f"视频节点 {node_id} 生成失败：provider 不可用且无 ffmpeg")

    async def _execute_ffmpeg(self, node_id: str, config: dict, deps: dict) -> Any:
        """Concatenate video artifacts produced by upstream nodes."""
        from ..services import generation
        ffmpeg = generation._ffmpeg_bin()
        if not ffmpeg:
            raise RuntimeError("ffmpeg 不可用，无法拼接成片")
        clips = []
        for dep_data in deps.values():
            if isinstance(dep_data, dict):
                for key in ("result", "video"):
                    v = dep_data.get(key)
                    if isinstance(v, str) and v.endswith(".mp4") and Path(v).exists():
                        clips.append(Path(v))
        if not clips:
            return {"node_id": node_id, "result": None, "skipped": "no upstream clips to stitch"}
        out_dir = Path(self.output_dir) / self.config.name
        out_dir.mkdir(parents=True, exist_ok=True)
        list_file = out_dir / f"{node_id}_list.txt"
        list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in clips), encoding="utf-8")
        target = out_dir / f"{node_id}_final.mp4"
        code = await generation._run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(target)])
        if code != 0:
            code = await generation._run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-pix_fmt", "yuv420p", str(target)])
        list_file.unlink(missing_ok=True)
        if code != 0 or not target.exists():
            raise RuntimeError("ffmpeg 拼接失败")
        return {"node_id": node_id, "result": str(target), "inputs": len(clips)}

    def _collect_output(self) -> dict:
        return {
            nid: state.output
            for nid, state in self.node_states.items()
            if state.output is not None
        }

    def get_status(self) -> dict:
        return {nid: state.to_dict() for nid, state in self.node_states.items()}