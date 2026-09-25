"""剪映草稿导出路由 — 把流水线产出的镜头片段组装成剪映可打开的草稿。

实际工作由工作区的 drama-pipeline/scripts/export_jianying_draft.py 完成
（pyJianYingDraft，已 pip 安装）。POST 一步到位：后端写临时清单 → 子进程
运行导出脚本 → 返回草稿路径。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/export", tags=["export"])

# ai-drama-studio/backend/app/api -> workspace/drama-pipeline
DEFAULT_SCRIPT = (
    Path(__file__).resolve().parents[4] / "drama-pipeline" / "scripts" / "export_jianying_draft.py"
)


class Shot(BaseModel):
    file: str = Field(..., description="镜头视频/图片路径（相对 backend 或绝对路径）")
    transition: str = Field(default="", description="剪映转场中文名，如 叠化；留空硬切")
    subtitle: str = Field(default="", description="该镜头字幕文本")


class JianyingExportRequest(BaseModel):
    name: str = Field(..., description="草稿名，如 我的新剧EP01")
    shots: List[Shot]
    bgm: Optional[str] = None
    bgm_volume: float = 0.4
    srt: Optional[str] = None
    width: int = 1080
    height: int = 1920
    fps: int = 30
    draft_root: str = Field(default="auto", description="剪映草稿根目录，auto 自动探测")
    run_export: bool = Field(default=False, description="调起剪映自动导出 MP4（需本机剪映 ≤6）")


@router.get("/jianying/info")
async def export_info():
    """脚本是否就位 + 剪映草稿目录探测结果。"""
    script = Path(os.getenv("JIANYING_EXPORT_SCRIPT", str(DEFAULT_SCRIPT)))
    return {
        "script_path": str(script),
        "script_available": script.is_file(),
        "draft_root": "auto",
    }


@router.post("/jianying")
async def export_jianying(req: JianyingExportRequest):
    script = Path(os.getenv("JIANYING_EXPORT_SCRIPT", str(DEFAULT_SCRIPT)))
    if not script.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"导出脚本不存在: {script}（应在工作区 drama-pipeline/scripts/）",
        )

    manifest = {
        "width": req.width,
        "height": req.height,
        "fps": req.fps,
        "shots": [s.model_dump() for s in req.shots],
        "bgm": req.bgm,
        "bgm_volume": req.bgm_volume,
        "srt": req.srt,
    }
    fd, manifest_path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)

    cmd = [
        sys.executable, str(script), manifest_path,
        "--name", req.name,
        "--draft-root", req.draft_root,
        "--fps", str(req.fps),
    ] + (["--export"] if req.run_export else [])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600, cwd=str(script.parent.parent),
        )
    finally:
        try:
            os.unlink(manifest_path)
        except OSError:
            pass

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"导出失败（exit {proc.returncode}）：{proc.stdout[-800:]}\n{proc.stderr[-800:]}",
        )
    draft_path = Path(req.draft_root) / req.name if req.draft_root != "auto" else None
    return {
        "ok": True,
        "name": req.name,
        "output": proc.stdout[-1500:],
        "draft_path": str(draft_path) if draft_path else None,
    }
