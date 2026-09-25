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
            return await self._execute_llm(node_config, deps)
        elif node_type == "comfyui":
            return await self._execute_comfyui(node_config, deps)
        elif node_type == "video":
            return await self._execute_video(node_config, deps)
        elif node_type == "ffmpeg":
            return await self._execute_ffmpeg(node_config, deps)
        else:
            raise ValueError(f"Unknown node type: {node_type}")

    async def _execute_llm(self, config: dict, deps: dict) -> Any:
        from ..llm.planner import plan_drama, generate_storyboard
        prompt_template = config.get("prompt_template", "")
        if prompt_template:
            # Load YAML template
            import yaml
            template_path = Path(__file__).parent.parent.parent / "config" / "prompts" / "scripts" / prompt_template
            with open(template_path, encoding="utf-8") as f:
                template = yaml.safe_load(f)
            prompt = template["prompt"]
            for k, v in deps.items():
                prompt = prompt.replace(f"{{{{{k}}}}}", json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
            from ..llm.planner import _openai_client
            response = await _openai_client.chat.completions.create(
                model=config.get("model", "gpt-4o"),
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        return {"node_id": node_id, "deps": deps, "result": "llm_placeholder"}

    async def _execute_comfyui(self, config: dict, deps: dict) -> Any:
        from ..providers.comfyui import ComfyUIClient
        workflow_path = Path(__file__).parent.parent.parent / "config" / "comfyui_workflows" / config.get("workflow_file", "default.json")
        client = ComfyUIClient()
        workflow = client.load_workflow(workflow_path)
        # Apply patches from deps
        patches = {}
        for dep_id, dep_data in deps.items():
            if isinstance(dep_data, dict):
                patches[dep_id] = dep_data
        if patches:
            workflow = ComfyUIClient.patch_workflow(workflow, patches)
        output_node = config.get("output_node", "9")
        output_dir = self.output_dir / self.config.name / "comfyui"
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = client.generate(workflow, output_node, output_dir / "output")
        return {"node_id": node_id, "paths": [str(p) for p in paths]}

    async def _execute_video(self, config: dict, deps: dict) -> Any:
        from ..providers.video_providers import get_provider
        provider_name = config.get("provider", "kling")
        provider = get_provider(provider_name)
        prompt = deps.get("prompt", "")
        result = await provider.generate(prompt=prompt)
        return {"node_id": "video", "result": result.url if hasattr(result, "url") else str(result)}

    async def _execute_ffmpeg(self, config: dict, deps: dict) -> Any:
        return {"node_id": "ffmpeg", "deps": deps, "result": "ffmpeg_placeholder"}

    def _collect_output(self) -> dict:
        return {
            nid: state.output
            for nid, state in self.node_states.items()
            if state.output is not None
        }

    def get_status(self) -> dict:
        return {nid: state.to_dict() for nid, state in self.node_states.items()}