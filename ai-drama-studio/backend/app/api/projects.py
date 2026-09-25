"""Project API routes."""
from fastapi import APIRouter, HTTPException
from ..models.chat import ProjectCreate, ProjectUpdate, ChatRequest, ChatResponse, CharacterCreate, ApprovalAction
from ..llm.planner import plan_drama, chat_with_planner, generate_storyboard as llm_generate_storyboard, LLMNotConfiguredError
from ..data.store import ProjectStore
from ..approval.gate import ApprovalGate
from ..providers.character_manager import CharacterManager
from ..services import generation
import os

router = APIRouter(prefix="/api/projects", tags=["projects"])

# Initialize shared state
_store = ProjectStore()
_approvals = {}
_char_managers = {}


def _get_store():
    return _store


def _get_approval_gate(project_id: str) -> ApprovalGate:
    if project_id not in _approvals:
        storage = os.path.join(os.path.dirname(__file__), "..", "..", "data")
        _approvals[project_id] = ApprovalGate(project_id, storage)
    return _approvals[project_id]


def _get_char_manager(project_id: str) -> CharacterManager:
    if project_id not in _char_managers:
        storage = os.path.join(os.path.dirname(__file__), "..", "..", "data")
        _char_managers[project_id] = CharacterManager(project_id, storage)
    return _char_managers[project_id]


@router.post("/")
async def create_project(req: ProjectCreate):
    project = _get_store().create(req.title, req.description or "")
    return project.to_dict()


@router.get("/")
async def list_projects():
    return [p.to_dict() for p in _get_store().list_all()]


@router.get("/{project_id}")
async def get_project(project_id: str):
    project = _get_store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project.to_dict()


@router.put("/{project_id}")
async def update_project(project_id: str, req: ProjectUpdate):
    project = _get_store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    updates = req.model_dump(exclude_unset=True)
    updated = _get_store().update(project_id, **updates)
    return updated.to_dict()


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    if not _get_store().delete(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": project_id}


@router.post("/{project_id}/plan")
async def plan_project(project_id: str):
    project = _get_store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        plan = await plan_drama(project.title, project.description)
    except LLMNotConfiguredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {e}")
    updated = _get_store().update_plan(project_id, plan)
    # auto-create approval items for the planned characters and episodes
    gate = _get_approval_gate(project_id)
    for char in updated.characters or []:
        gate.create_item("character", "character", str(char.get("id") or char.get("name")),
                         char.get("name", "角色"), char.get("appearance", ""))
    for ep in updated.episodes or []:
        gate.create_item("episode", "episode", str(ep.get("number")),
                         ep.get("title", f"第{ep.get('number')}集"), ep.get("summary", ""))
    return updated.to_dict()


@router.post("/{project_id}/chat")
async def chat(project_id: str, req: ChatRequest):
    project = _get_store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        context = project.to_dict()
        result = await chat_with_planner(context, req.message)
        return {"reply": result["reply"], "suggestions": result.get("suggestions", [])}
    except LLMNotConfiguredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {e}")


@router.post("/{project_id}/characters")
async def add_character(project_id: str, req: "CharacterCreate"):
    
    cm = _get_char_manager(project_id)
    char = cm.add_character(req.name, req.role, req.description, req.appearance, req.costume, req.personality)
    # Also update project
    project = _get_store().get(project_id)
    project.characters.append(char.to_dict())
    _get_store()._save(project)
    return char.to_dict()


@router.get("/{project_id}/characters")
async def list_characters(project_id: str):
    cm = _get_char_manager(project_id)
    return [c.to_dict() for c in cm.list_characters()]


@router.post("/{project_id}/storyboard/episode/{episode_num}")
async def storyboard_episode(project_id: str, episode_num: int):
    project = _get_store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    episode = next((e for e in project.episodes if e.get("number") == episode_num), None)
    if not episode:
        raise HTTPException(status_code=404, detail=f"Episode {episode_num} not found")
    try:
        result = await llm_generate_storyboard(episode, project.characters)
    except LLMNotConfiguredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {e}")
    project.storyboard.append({"episode": episode_num, "shots": result.get("shots", [])})
    _get_store()._save(project)
    return result


# ------------------------------------------------------------------ generation

@router.post("/{project_id}/generate/characters")
async def generate_characters(project_id: str):
    """Generate a reference sheet for every character (ComfyUI, else placeholder)."""
    project = _get_store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.characters:
        raise HTTPException(status_code=400, detail="还没有角色，请先生成剧情规划")
    results = []
    for char in project.characters:
        r = await generation.generate_character_image(project_id, char)
        results.append(r)
    assets = project.assets or {}
    assets["characters"] = {r["character_id"]: r.get("image") for r in results if r.get("image")}
    project.assets = assets
    _get_store()._save(project)
    return {"results": results}


@router.post("/{project_id}/generate/storyboard/{episode_num}")
async def generate_storyboard_images(project_id: str, episode_num: int):
    project = _get_store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    episode = next((e for e in project.episodes if e.get("number") == episode_num), None)
    if not episode:
        raise HTTPException(status_code=404, detail=f"Episode {episode_num} not found")
    sb = next((s for s in project.storyboard if s.get("episode") == episode_num), None)
    if sb:
        episode = {**episode, "shots": sb.get("shots", [])}
    result = await generation.generate_storyboard_images(project_id, episode)
    assets = project.assets or {}
    assets.setdefault("storyboard", {})[str(episode_num)] = result["images"]
    project.assets = assets
    _get_store()._save(project)
    return result


@router.post("/{project_id}/generate/video/{episode_num}")
async def generate_video(project_id: str, episode_num: int):
    project = _get_store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    episode = next((e for e in project.episodes if e.get("number") == episode_num), None)
    if not episode:
        raise HTTPException(status_code=404, detail=f"Episode {episode_num} not found")
    sb = next((s for s in project.storyboard if s.get("episode") == episode_num), None)
    result = await generation.generate_episode_video(
        project_id, episode, {"shots": sb.get("shots", [])} if sb else None
    )
    if not result.get("video"):
        raise HTTPException(status_code=500, detail=result.get("error", "视频生成失败"))
    assets = project.assets or {}
    assets.setdefault("videos", {})[str(episode_num)] = result["video"]
    project.assets = assets
    if all(str(e.get("number")) in assets.get("videos", {}) for e in project.episodes):
        project.status = "completed"
    _get_store()._save(project)
    return result


@router.get("/{project_id}/approvals")
async def list_approvals(project_id: str, stage: str = None):
    gate = _get_approval_gate(project_id)
    if stage:
        return [a.to_dict() for a in gate.get_by_stage(stage)]
    return [a.to_dict() for a in gate.get_all()]


@router.post("/{project_id}/approvals")
async def create_approval(project_id: str, body: dict):
    """Create an approval item (action=create from the frontend)."""
    if body.get("action") != "create":
        raise HTTPException(status_code=400, detail="Only action=create is supported here")
    gate = _get_approval_gate(project_id)
    item = gate.create_item(
        stage=body.get("stage", "general"),
        item_type=body.get("itemType", body.get("item_type", "generic")),
        item_id=str(body.get("itemId", uuid.uuid4().hex[:6])),
        title=body.get("title", ""),
        description=body.get("description", ""),
    )
    return item.to_dict()


@router.post("/{project_id}/approvals/{item_id}/action")
async def action_approval(project_id: str, item_id: str, req: "ApprovalAction"):
    
    gate = _get_approval_gate(project_id)
    if req.action == "approve":
        item = gate.approve(item_id)
    elif req.action == "reject":
        item = gate.reject(item_id, req.feedback)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
    if not item:
        raise HTTPException(status_code=404, detail="Approval item not found")
    return item.to_dict()