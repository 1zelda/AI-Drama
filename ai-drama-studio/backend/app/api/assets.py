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


def _fix_entry(a: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """自愈历史/换机条目：文件按文件名重新锚定回本目录，补齐缺失字段；文件真丢了返回 None。"""
    p = Path(a.get("path") or "")
    if not p.exists():
        fid = a.get("id") or ""
        cands = [ASSET_DIR / p.name]
        if fid and p.name:
            cands.append(ASSET_DIR / f"{fid}_{p.name}")
        p = next((c for c in cands if c.exists()), None)
        if p is None:
            return None
    a["path"] = str(p)
    fid = a.get("id") or ""
    if not a.get("filename"):
        a["filename"] = p.name[len(fid) + 1:] if fid and p.name.startswith(fid + "_") else p.name
    ext = p.suffix.lower()
    a.setdefault("kind", "image" if ext in IMAGE_EXTS else "video" if ext in VIDEO_EXTS else "other")
    a.setdefault("url", _asset_url(fid, a["filename"]))
    a.setdefault("tags", [])
    a.setdefault("note", "")
    a.setdefault("size", p.stat().st_size)
    a.setdefault("created_at", p.stat().st_mtime)
    return a


def _load_repaired() -> Dict[str, Dict[str, Any]]:
    """读索引并自愈：丢文件的条目剔除、缺字段的补齐、把目录里没登记的散文件补录进来。"""
    index = _load_index()
    changed = False
    fixed: Dict[str, Dict[str, Any]] = {}
    for aid, a in index.items():
        f = _fix_entry({**a, "id": aid})
        if f:
            if f != a:
                changed = True
            fixed[aid] = f
        else:
            changed = True
    known = {Path(a["path"]).name for a in fixed.values()}
    if ASSET_DIR.exists():
        for f in sorted(ASSET_DIR.iterdir()):
            if not f.is_file() or f.name == "index.json" or f.name in known:
                continue
            aid, _, rest = f.name.partition("_")
            if not rest or len(aid) != 10:
                continue
            ext = f.suffix.lower()
            fixed[aid] = {
                "id": aid, "filename": rest,
                "kind": "image" if ext in IMAGE_EXTS else "video" if ext in VIDEO_EXTS else "other",
                "path": str(f), "url": _asset_url(aid, rest), "tags": [], "note": "",
                "size": f.stat().st_size, "created_at": f.stat().st_mtime,
            }
            changed = True
    if changed:
        _save_index(fixed)
    return fixed


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
    index = _load_repaired()
    items = list(index.values())
    if kind:
        items = [a for a in items if a["kind"] == kind]
    if tag:
        items = [a for a in items if tag in a["tags"]]
    items.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return items


@router.get("/{asset_id}")
async def get_asset(asset_id: str):
    asset = _load_repaired().get(asset_id)
    if not asset:
        raise HTTPException(404, f"资产不存在：{asset_id}")
    return asset


@router.get("/{asset_id}/file")
async def asset_file(asset_id: str):
    asset = _load_repaired().get(asset_id)
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
