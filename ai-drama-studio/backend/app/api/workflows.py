"""Workflow API routes."""
from fastapi import APIRouter, HTTPException
from pathlib import Path
import json

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
config_dir = Path(__file__).parent.parent.parent / "config" / "comfyui_workflows"


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


@router.get("/{name}")
async def get_workflow(name: str):
    workflow_path = config_dir / f"{name}.json"
    if not workflow_path.exists():
        raise HTTPException(status_code=404, detail="Workflow not found")
    with open(workflow_path, encoding="utf-8") as f:
        return json.load(f)