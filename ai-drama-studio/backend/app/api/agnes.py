"""Agnes Video 2.5 API routes.

Generation is asynchronous: POST creates a task and returns immediately with a
`video_id`, then GET polls it. The previous revision blocked the request for the
whole generation (30s–5min), which reliably timed out in front of proxies.

Local assets: `first_frame` / `last_frame` / `images` accept either a public URL
or a local path. A local path is published through the asset host, which needs
PUBLIC_ASSET_BASE_URL to be set (see app/providers/asset_host.py).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/agnes", tags=["agnes"])

# backend/output -> served at /output
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# In-memory task registry. Enough for a single-node deployment; swap for Redis
# if you run multiple workers.
_TASKS: Dict[str, Dict[str, Any]] = {}


class VideoGenerateRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    mode: Optional[str] = Field(
        default=None, description="text | keyframe | reference (inferred when omitted)"
    )
    seconds: Any = Field(default=5, description="4-12, sent to Agnes as a string")
    size: str = Field(default="720P", description="720P | 960P | 2K")
    aspect_ratio: str = Field(
        default="16:9", description="21:9 | 16:9 | 4:3 | 1:1 | 3:4 | 9:16"
    )
    seed: Optional[int] = None
    first_frame: Optional[str] = None
    last_frame: Optional[str] = None
    images: Optional[List[str]] = None
    audios: Optional[List[str]] = None
    videos: Optional[List[Dict[str, Any]]] = None
    download: bool = Field(default=True, description="Save the MP4 under backend/output")

    # Backwards-compatible aliases used by older clients.
    resolution: Optional[str] = None
    duration: Optional[Any] = None


class VideoGenerateResponse(BaseModel):
    video_id: Optional[str] = None
    task_id: Optional[str] = None
    status: str
    url: Optional[str] = None
    local_path: Optional[str] = None
    progress: int = 0
    error: Optional[str] = None


def _client():
    from ..providers.agnes import get_agnes_client

    return get_agnes_client()


def _asset_host():
    from ..providers.asset_host import get_asset_host

    return get_asset_host()


def _publish(value: Optional[str], node: str = "api") -> Optional[str]:
    """Local path -> public URL; public URLs pass through unchanged."""
    if not value:
        return None
    text = str(value)
    if text.startswith(("http://", "https://")):
        return text
    return _asset_host().publish(text, subdir=node)


def _normalise(req: VideoGenerateRequest) -> Dict[str, Any]:
    """Map the request onto the documented Agnes parameter names."""
    size = (req.size or req.resolution or "720P").strip()
    size_upper = size.upper()
    # Accept the legacy lowercase/px spellings instead of 400-ing.
    size_alias = {"720P": "720P", "960P": "960P", "2K": "2K", "1080P": "960P"}
    size = size_alias.get(size_upper, size_upper)

    seconds = req.seconds if req.seconds not in (None, "") else (req.duration or 5)

    mode = req.mode
    if mode:
        mode = mode.lower()
        mode_alias = {"t2v": "text", "i2v": "keyframe", "r2v": "reference"}
        mode = mode_alias.get(mode, mode)

    return {
        "prompt": req.prompt,
        "model": req.model,
        "mode": mode,
        "seconds": seconds,
        "size": size,
        "aspect_ratio": req.aspect_ratio,
        "seed": req.seed,
        "first_frame": _publish(req.first_frame),
        "last_frame": _publish(req.last_frame),
        "images": [_publish(i) for i in req.images] if req.images else None,
        "audios": req.audios,
        "videos": req.videos,
    }


async def _run_task(video_id: str, model_name: str, download: bool):
    """Background worker: poll the task, then optionally fetch the MP4."""
    entry = _TASKS.setdefault(video_id, {"video_id": video_id, "status": "queued", "progress": 0})
    client = _client()
    try:
        result = await client.wait_for_completion(
            video_id,
            model_name=model_name,
            on_progress=lambda p, s: entry.update(progress=p, status=s),
        )
        entry.update(
            status="completed",
            progress=100,
            url=result.url,
            model=result.model,
            seconds=result.seconds,
            size=result.size,
        )
        if download and result.url:
            path = await client.download(result.url, str(OUTPUT_DIR), f"{video_id}.mp4")
            entry["local_path"] = str(path)
            entry["local_url"] = f"/output/{video_id}.mp4"
    except Exception as exc:  # noqa: BLE001 - surfaced to the client via status
        entry.update(status="failed", error=str(exc))
    finally:
        await client.close()


@router.post("/video/generate", response_model=VideoGenerateResponse)
async def generate_video(req: VideoGenerateRequest, background: BackgroundTasks):
    """Create a video task and return immediately.

    Poll `GET /api/agnes/video/{video_id}` until status is completed/failed.
    Generation typically takes 30s–5min, so this must not block.
    """
    client = _client()
    try:
        kwargs = _normalise(req)
        payload = client.build_payload(**kwargs)
        created = await client.create_task(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))

    video_id = created["video_id"]
    _TASKS[video_id] = {
        "video_id": video_id,
        "task_id": created.get("task_id") or video_id,
        "status": created.get("status", "queued"),
        "progress": int(created.get("progress") or 0),
        "model": payload["model"],
        "mode": payload["mode"],
    }
    background.add_task(_run_task, video_id, payload["model"], req.download)

    return VideoGenerateResponse(
        video_id=video_id,
        task_id=_TASKS[video_id]["task_id"],
        status=_TASKS[video_id]["status"],
        progress=_TASKS[video_id]["progress"],
    )


@router.get("/video/{video_id}", response_model=VideoGenerateResponse)
async def get_video(video_id: str):
    """Poll a task. Falls back to the API when it is not in the local registry
    (e.g. after a backend restart)."""
    entry = _TASKS.get(video_id)
    if entry:
        return VideoGenerateResponse(
            video_id=video_id,
            task_id=entry.get("task_id"),
            status=entry.get("status", "unknown"),
            url=entry.get("url"),
            local_path=entry.get("local_path") or entry.get("local_url"),
            progress=int(entry.get("progress") or 0),
            error=entry.get("error"),
        )

    client = _client()
    try:
        body = await client.get_task(video_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))

    from ..providers.agnes import AgnesVideoClient

    result = AgnesVideoClient._to_result(body, video_id)
    return VideoGenerateResponse(
        video_id=video_id,
        task_id=body.get("task_id") or video_id,
        status=body.get("status", "unknown"),
        url=result.url,
        progress=int(body.get("progress") or 0),
        error=(body.get("error") or {}).get("message") if body.get("error") else None,
    )


@router.post("/video/generate-and-wait", response_model=VideoGenerateResponse)
async def generate_and_wait(req: VideoGenerateRequest):
    """Blocking variant — convenient for scripts/tests, not for the UI."""
    client = _client()
    try:
        kwargs = _normalise(req)
        result = await client.generate(
            **kwargs,
            output_dir=str(OUTPUT_DIR) if req.download else None,
            max_wait=900,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))

    return VideoGenerateResponse(
        video_id=result.video_id,
        task_id=result.task_id,
        status=result.status,
        url=result.url,
        local_path=result.local_path
        or (f"/output/{result.video_id}.mp4" if req.download else None),
        progress=100,
    )


@router.get("/models")
async def list_models():
    client = _client()
    return {"models": await client.list_models()}


@router.get("/health")
async def check_health():
    client = _client()
    return await client.health()


@router.get("/assets/status")
async def asset_status():
    """Whether keyframe/reference modes can work (needs a public asset URL)."""
    return _asset_host().status()


@router.get("/providers")
async def providers():
    from ..providers.video_providers import list_providers

    return {"providers": list_providers()}


@router.get("/tasks")
async def list_tasks(limit: int = 50):
    return {"tasks": list(_TASKS.values())[-limit:]}
