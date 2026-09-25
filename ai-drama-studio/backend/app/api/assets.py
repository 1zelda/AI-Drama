"""Local asset library: reference images / videos for the pipeline.

Uploads land in ``data/assets/`` and are served statically at ``/media/assets``.
Assets can be tagged (e.g. ``character:林晚``, ``scene:照相馆``, ``style:cinematic``)
so the storyboard planner can reference them by tag.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/assets", tags=["assets"])

ASSET_DIR = Path(__file__).parent.parent.parent / "data" / "assets"
INDEX_FILE = ASSET_DIR / "index.json"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}


def _load_index() -> Dict[str, Dict[str, Any]]:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_index(index: Dict[str, Dict[str, Any]]) -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _asset_url(asset_id: str, filename: str) -> str:
    return f"/media/assets/{asset_id}_{filename}"


@router.post("/upload")
async def upload_asset(
    file: UploadFile = File(...),
    tags: str = Form(""),
    note: str = Form(""),
):
    """Upload one image/video. ``tags`` is comma-separated."""
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    original = Path(file.filename or "asset.bin")
    ext = original.suffix.lower()
    kind = "image" if ext in IMAGE_EXTS else "video" if ext in VIDEO_EXTS else "other"
    if kind == "other":
        raise HTTPException(400, f"不支持的文件类型：{ext}（图片：{sorted(IMAGE_EXTS)}，视频：{sorted(VIDEO_EXTS)}）")

    asset_id = uuid.uuid4().hex[:10]
    dest = ASSET_DIR / f"{asset_id}_{original.name}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    size = dest.stat().st_size

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    index = _load_index()
    index[asset_id] = {
        "id": asset_id,
        "filename": original.name,
        "kind": kind,
        "path": str(dest),
        "url": _asset_url(asset_id, original.name),
        "tags": tag_list,
        "note": note,
        "size": size,
        "created_at": time.time(),
    }
    _save_index(index)
    return index[asset_id]


@router.get("/")
async def list_assets(kind: Optional[str] = None, tag: Optional[str] = None):
    index = _load_index()
    items = list(index.values())
    if kind:
        items = [a for a in items if a["kind"] == kind]
    if tag:
        items = [a for a in items if tag in a["tags"]]
    items.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return items


@router.get("/{asset_id}")
async def get_asset(asset_id: str):
    asset = _load_index().get(asset_id)
    if not asset:
        raise HTTPException(404, f"资产不存在：{asset_id}")
    return asset


@router.get("/{asset_id}/file")
async def asset_file(asset_id: str):
    asset = _load_index().get(asset_id)
    if not asset or not Path(asset["path"]).exists():
        raise HTTPException(404, f"资产不存在或文件丢失：{asset_id}")
    return FileResponse(asset["path"])


@router.put("/{asset_id}/tags")
async def update_tags(asset_id: str, body: Dict[str, Any]):
    index = _load_index()
    if asset_id not in index:
        raise HTTPException(404, f"资产不存在：{asset_id}")
    tags = body.get("tags") or []
    index[asset_id]["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if "note" in body:
        index[asset_id]["note"] = str(body["note"])
    _save_index(index)
    return index[asset_id]


@router.delete("/{asset_id}")
async def delete_asset(asset_id: str):
    index = _load_index()
    asset = index.pop(asset_id, None)
    if not asset:
        raise HTTPException(404, f"资产不存在：{asset_id}")
    try:
        Path(asset["path"]).unlink(missing_ok=True)
    except OSError:
        pass  # file may already be gone; index entry is what matters
    _save_index(index)
    return {"deleted": asset_id}
