"""镜头路由 API：读取/修改 shot_routing.json（UI 的路由设置页用）。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..orchestrator.shot_routing import load_routing, resolve_shot_type, save_routing

router = APIRouter(prefix="/api/routing", tags=["routing"])


@router.get("/")
async def get_routing():
    return load_routing()


@router.put("/")
async def put_routing(data: Dict[str, Any]):
    if "shot_types" not in data:
        raise HTTPException(status_code=400, detail="缺少 shot_types")
    save_routing(data)
    return load_routing()


class PreviewRequest(BaseModel):
    shot_type: str


@router.post("/preview")
async def preview(req: PreviewRequest):
    """给定镜头类型，返回将被派发到哪个模型 + 运动提示词模板。"""
    info = resolve_shot_type(req.shot_type)
    if not info:
        raise HTTPException(status_code=404, detail=f"未知镜头类型 {req.shot_type!r}")
    return {
        "shot_type": info,
        "provider": info.get("preferred"),
        "fallback": info.get("fallback"),
        "banned": info.get("banned", []),
        "motion_template": info.get("motion_template"),
        "watermark": info.get("watermark"),
    }
