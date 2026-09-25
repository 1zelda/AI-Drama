"""Agnes API routes for video generation."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

router = APIRouter(prefix="/api/agnes", tags=["agnes"])

class VideoGenerateRequest(BaseModel):
    prompt: str
    model: Optional[str] = "agnes-video-2.5-flash"
    resolution: str = "720p"
    duration: int = 5
    seed: Optional[int] = None
    mode: Optional[str] = "T2V"

class VideoGenerateResponse(BaseModel):
    task_id: Optional[str]
    video_url: Optional[str]
    status: str
    error: Optional[str] = None

@router.post("/video/generate", response_model=VideoGenerateResponse)
async def generate_video(req: VideoGenerateRequest):
    from ..providers.agnes import get_agnes_client
    client = get_agnes_client()
    try:
        result = await client.generate(
            prompt=req.prompt,
            model=req.model,
            resolution=req.resolution,
            duration=req.duration,
            seed=req.seed,
            mode=req.mode,
        )
        return VideoGenerateResponse(
            task_id=result.get("task_id"),
            video_url=result.get("video_url"),
            status=result.get("status", "unknown"),
        )
    except Exception as e:
        return VideoGenerateResponse(status="error", error=str(e))

@router.get("/models")
async def list_models():
    from ..providers.agnes import get_agnes_client
    client = get_agnes_client()
    try:
        models = await client.list_models()
        return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}

@router.get("/health")
async def check_health():
    from ..providers.agnes import get_agnes_client
    client = get_agnes_client()
    ok = await client.health()
    return {"status": "ok" if ok else "unavailable"}
