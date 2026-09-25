"""Settings API routes."""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json
import os
from pathlib import Path

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_PATH = Path(__file__).parent.parent.parent.parent / "frontend" / "data" / "settings.json"

DEFAULT_SETTINGS = {
    "llm_provider": "deepseek",
    "api_key": "",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "comfyui_url": "http://localhost:8188",
    "comfyui_available": False,
    "agnes_api_key": "",
    "agnes_proxy_url": "http://127.0.0.1:57324",
    "agnes_video_model": "agnes-video-2.5-flash",
    "agnes_video_mode": "T2V",
    "kling_api_key": "",
    "kling_api_url": "https://api.klingai.com/v1",
    "seedance_api_key": "",
    "veo_api_key": "",
}


def _load_settings() -> Dict[str, Any]:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def _save_settings(data: Dict[str, Any]):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Also sync to backend .env
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    env_lines = []
    env_lines.append(f'OPENAI_API_KEY={data.get("api_key", "")}')
    env_lines.append(f'OPENAI_BASE_URL={data.get("base_url", "https://api.deepseek.com/v1")}')
    env_lines.append(f'LLM_MODEL={data.get("model", "deepseek-chat")}')
    env_lines.append(f'COMFYUI_SERVER_URL={data.get("comfyui_url", "http://localhost:8188")}')
    env_lines.append(f'AGNES_API_KEY={data.get("agnes_api_key", "")}')
    env_lines.append(f'AGNES_PROXY_URL={data.get("agnes_proxy_url", "http://127.0.0.1:57324")}')
    env_lines.append(f'AGNES_VIDEO_MODEL={data.get("agnes_video_model", "agnes-video-2.5-flash")}')
    env_lines.append(f'AGNES_VIDEO_MODE={data.get("agnes_video_mode", "T2V")}')
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")


@router.get("/check")
async def check_comfyui():
    """Probe the configured ComfyUI server and cache availability in settings."""
    import httpx
    settings = _load_settings()
    url = (settings.get("comfyui_url") or "http://localhost:8188").rstrip("/")
    available = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{url}/system_stats")
            available = resp.status_code == 200
    except Exception:
        available = False
    if settings.get("comfyui_available") != available:
        settings["comfyui_available"] = available
        _save_settings(settings)
    return {"available": available, "url": url}


@router.get("/")
async def get_settings():
    return _load_settings()


@router.put("/")
@router.post("/")
async def update_settings(req: Dict[str, Any]):
    settings = _load_settings()
    settings.update(req)
    _save_settings(settings)
    return settings
