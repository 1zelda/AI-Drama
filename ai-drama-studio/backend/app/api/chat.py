"""Chat API route."""
from fastapi import APIRouter, HTTPException
from ..models.chat import ChatRequest, ChatResponse
from ..data.store import ProjectStore
from ..llm.planner import chat_with_planner

router = APIRouter(prefix="/api/chat", tags=["chat"])
_store = ProjectStore()


@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest):
    project_id = req.context.get("project_id") if req.context else None
    if project_id:
        project = _store.get(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        context = project.to_dict()
    else:
        context = {}
    try:
        result = await chat_with_planner(context, req.message)
        return ChatResponse(
            reply=result["reply"],
            suggestions=result.get("suggestions", []),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))