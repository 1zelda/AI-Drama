"""Workflow API routes."""
from fastapi import APIRouter, HTTPException
from pathlib import Path
import json

from ..models.chat import WorkflowExecuteRequest
from ..orchestrator.engine import WorkflowConfig, WorkflowOrchestrator

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
backend_dir = Path(__file__).parent.parent.parent
config_dir = backend_dir / "config" / "comfyui_workflows"
workflow_dir = backend_dir / "config" / "workflows"


@router.get("/")
async def list_workflows():
    workflows = []
    if config_dir.exists():
        for fp in sorted(config_dir.glob("*.json")):
            with open(fp, encoding="utf-8") as f:
                wf = json.load(f)
                workflows.append({
                    "name": wf.get("name", fp.stem),
                    "description": wf.get("description", ""),
                    "type": wf.get("type", "image"),
                    "output_node": wf.get("output_node", "9"),
                })
    return workflows


@router.get("/pipelines")
async def list_pipelines():
    """List DAG orchestration pipelines (config/workflows/*.json)."""
    pipelines = []
    if workflow_dir.exists():
        for fp in sorted(workflow_dir.glob("*.json")):
            try:
                cfg = WorkflowConfig(fp)
                pipelines.append({
                    "name": cfg.name,
                    "description": cfg.description,
                    "nodes": list(cfg.nodes.keys()),
                    "execution_order": cfg.get_execution_order(),
                })
            except Exception as e:
                pipelines.append({"name": fp.stem, "error": str(e), "nodes": [], "execution_order": []})
    return pipelines


@router.post("/{name}/execute")
async def execute_workflow(name: str, req: WorkflowExecuteRequest):
    """Run a DAG pipeline end-to-end and return per-node status."""
    path = workflow_dir / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Workflow {name} not found")
    try:
        cfg = WorkflowConfig(path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"工作流配置无效: {e}")

    orchestrator = WorkflowOrchestrator(cfg, output_dir=str(backend_dir / "data" / "output"))
    try:
        output = await orchestrator.execute(req.input_data or {})
        status = "completed"
    except Exception as e:
        output = orchestrator._collect_output()
        status = "failed"
        error = str(e)
    else:
        error = None

    return {
        "workflow_name": cfg.name,
        "status": status,
        "error": error,
        "node_states": orchestrator.get_status(),
        "output": output,
    }


@router.get("/{name}")
async def get_workflow(name: str):
    workflow_path = config_dir / f"{name}.json"
    if not workflow_path.exists():
        raise HTTPException(status_code=404, detail="Workflow not found")
    with open(workflow_path, encoding="utf-8") as f:
        return json.load(f)
