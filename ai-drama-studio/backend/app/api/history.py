"""成片库：把每次跑出来的成片落库，别再「刷新一下就找不到了」。

以前一键成片跑完，成片只躺在 output/ 里，跟一堆中间产物混在一起，
浏览器一刷新连 run_id 都没了。这里在 run 结束时把成片登记进
``data/history.json``，顺手截一张封面，供「成片库」页面按时间倒序浏览、
在线播放、下载、删除。

落库的只有成片和封面；中间产物（分镜图、单镜头视频、配音）由
``/api/system/cleanup`` 负责清理，两边靠这个文件区分「该留的」。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/history", tags=["history"])

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_ROOT / "data"
OUTPUT_DIR = BACKEND_ROOT / "output"
HISTORY_FILE = DATA_DIR / "history.json"
POSTER_DIR = OUTPUT_DIR / "posters"

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


def _load() -> List[Dict[str, Any]]:
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _save(items: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _to_url(path: Path) -> Optional[str]:
    try:
        rel = Path(path).resolve().relative_to(OUTPUT_DIR.resolve())
    except Exception:
        return None
    return f"/media/output/{rel.as_posix()}"


def _find_final_video(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从一次 run 的 {node_id: output} 里挑出「成片」。

    优先 final_cut / 名字里带 final 的，否则取最后一个产出视频的节点。
    """
    candidates: List[Dict[str, Any]] = []

    def walk(value: Any, node_id: Optional[str]):
        if isinstance(value, dict):
            nid = value.get("node_id") or node_id
            for key in ("path", "local_path"):
                p = value.get(key)
                if isinstance(p, str) and Path(p).suffix.lower() in VIDEO_EXTS:
                    candidates.append({"path": p, "node_id": nid or ""})
                    return
            for v in value.values():
                walk(v, nid)
        elif isinstance(value, list):
            for item in value:
                walk(item, node_id)

    for nid, out in (result or {}).items():
        walk(out, nid)

    if not candidates:
        return None
    for c in candidates:
        if "final" in str(c["node_id"]).lower() or "final" in Path(c["path"]).name.lower():
            return c
    return candidates[-1]


async def _make_poster(video: Path, dest: Path) -> Optional[str]:
    """截一张封面（取 15% 处，避开片头黑帧）。"""
    try:
        from ..services.postprod import probe

        info = await probe(str(video))
        seek = max(0.5, (info.get("duration") or 5) * 0.15)
    except Exception:
        seek = 1.0
    import asyncio

    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-ss", f"{seek:.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=480:-2", "-loglevel", "error", str(dest),
    )
    await proc.communicate()
    return str(dest) if dest.exists() else None


async def record_run(run_id: str, workflow: str, result: Dict[str, Any],
                     input_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """run 成功结束时调用；没有成片就返回 None（不算失败）。"""
    found = _find_final_video(result or {})
    if not found:
        return None
    video = Path(found["path"]).resolve()
    if not video.exists():
        return None

    title = ""
    script = (result or {}).get("script_planner")
    if isinstance(script, dict):
        title = str(script.get("title") or "")
    if not title:
        title = str((input_data or {}).get("title") or "")[:40]

    meta: Dict[str, Any] = {}
    final_out = (result or {}).get(found["node_id"])
    if isinstance(final_out, dict):
        meta = {k: v for k, v in final_out.items()
                if k in ("duration", "width", "height", "clips")}

    record_id = uuid.uuid4().hex[:12]
    poster = POSTER_DIR / f"{record_id}.jpg"
    poster_path = await _make_poster(video, poster)

    item = {
        "id": record_id,
        "run_id": run_id,
        "workflow": workflow,
        "title": title,
        "idea": str((input_data or {}).get("title") or "")[:200],
        "node_id": found["node_id"],
        "created_at": time.time(),
        "video_path": str(video),
        "video_url": _to_url(video),
        "poster_path": poster_path,
        "poster_url": _to_url(Path(poster_path)) if poster_path else None,
        "size": video.stat().st_size,
        **{k: v for k, v in meta.items()},
    }

    items = _load()
    items.append(item)
    items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    _save(items)
    return item


def _relocate(it: Dict[str, Any]) -> bool:
    """项目整体搬家后 video_path 会失效；用相对 URL 把文件重新锚定回本地 output。"""
    changed = False
    for path_key, url_key in (("video_path", "video_url"), ("poster_path", "poster_url")):
        p = it.get(path_key)
        if not p or Path(p).exists():
            continue
        url = it.get(url_key)
        if url and url.startswith("/media/output/"):
            cand = OUTPUT_DIR / url[len("/media/output/"):]
            if cand.exists():
                it[path_key] = str(cand.resolve())
                changed = True
    return changed


@router.get("/")
async def list_history(limit: int = Query(100, ge=1, le=500)):
    items = _load()
    if sum(_relocate(it) for it in items):
        _save(items)
    items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    # 文件可能已被外部删除，返回时标注出来，页面上给个提示而不是展示坏图
    for it in items:
        it["exists"] = bool(it.get("video_path") and Path(it["video_path"]).exists())
    return {"items": items[:limit], "total": len(items)}


@router.get("/{record_id}")
async def get_history(record_id: str):
    for it in _load():
        if it.get("id") == record_id:
            return it
    raise HTTPException(404, f"成片记录不存在：{record_id}")


@router.delete("/{record_id}")
async def delete_history(record_id: str, keep_files: bool = Query(
        False, description="true 只删记录，保留视频文件")):
    items = _load()
    target = next((i for i in items if i.get("id") == record_id), None)
    if not target:
        raise HTTPException(404, f"成片记录不存在：{record_id}")
    removed = []
    if not keep_files:
        for key in ("video_path", "poster_path"):
            p = target.get(key)
            if not p:
                continue
            try:
                Path(p).unlink(missing_ok=True)
                removed.append(p)
            except OSError:
                pass
    _save([i for i in items if i.get("id") != record_id])
    return {"deleted": record_id, "files_removed": removed, "keep_files": keep_files}


@router.post("/prune-missing")
async def prune_missing():
    """清掉那些视频文件已经不在了的记录（比如手动删过 output/）。"""
    items = _load()
    kept = [i for i in items if i.get("video_path") and Path(i["video_path"]).exists()]
    removed = len(items) - len(kept)
    if removed:
        _save(kept)
    return {"removed": removed, "kept": len(kept)}
