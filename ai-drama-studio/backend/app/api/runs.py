"""Pipeline run API: start a workflow, poll status, stream events over SSE."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..orchestrator import WorkflowConfig, WorkflowOrchestrator
from ..orchestrator.events import bus
from ..services import workflow_store
from ..services.run_store import RunStore

router = APIRouter(prefix="/api/runs", tags=["runs"])

# run 状态落盘：后端重启后 /api/runs/{id} 仍能查到结果，而且能断点续跑
RUN_STORE = RunStore()

WORKFLOW_DIR = Path(__file__).parent.parent.parent / "config" / "workflows"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "output"

# run_id -> asyncio.Task
_tasks: Dict[str, asyncio.Task] = {}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}

# 媒体尺寸缓存：path -> ((mtime, size), {width,height})。视频要 ffprobe，不能每次都探测。
_SIZE_CACHE: Dict[str, tuple] = {}


def _list_workflows() -> list:
    return workflow_store.list_workflows()


def _to_url(path: str) -> Optional[str]:
    """把本地绝对路径换成浏览器能访问的 /media/... URL。"""
    try:
        p = Path(path).resolve()
    except Exception:
        return None
    for base, prefix in ((OUTPUT_DIR, "/media/output"),
                         (Path(__file__).parent.parent.parent / "data" / "assets", "/media/assets")):
        try:
            rel = p.relative_to(base.resolve())
        except Exception:
            continue
        return f"{prefix}/" + rel.as_posix()
    if p.exists():
        return f"/media/output/" + p.name  # 兜底，至少不 404
    return None


def _artifact_kind(path: str) -> Optional[str]:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def _image_size(path: Path) -> Dict[str, int]:
    """图片宽高（PIL 直读，比起 ffprobe 便宜得多）。前端据此按原比例排版缩略图。"""
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
        return {"width": int(w), "height": int(h)}
    except Exception:
        return {}


async def _media_size(path: Path, kind: str) -> Dict[str, int]:
    """媒体真实宽高。前端据此按原比例排版缩略图，横竖屏都不会被压扁或裁掉。

    图片走 PIL（几乎零成本）；视频要 ffprobe，所以按 (mtime,size) 缓存，
    避免每次刷新产物都开几十个子进程。
    """
    if kind == "image":
        return _image_size(path)
    try:
        st = path.stat()
    except OSError:
        return {}
    key = str(path)
    hit = _SIZE_CACHE.get(key)
    if hit and hit[0] == (st.st_mtime, st.st_size):
        return hit[1]
    try:
        from ..services.postprod import probe

        info = await probe(str(path))
        size = {"width": int(info.get("width") or 0), "height": int(info.get("height") or 0)}
        size = size if size["width"] and size["height"] else {}
    except Exception:
        size = {}
    _SIZE_CACHE[key] = ((st.st_mtime, st.st_size), size)
    return size


async def _collect_artifacts(result: Any) -> List[Dict[str, Any]]:
    """从一次 run 的 {node_id: output} 里抽出所有可预览的图片/视频。"""
    found: Dict[str, Dict[str, Any]] = {}

    def walk(value: Any, node_id: Optional[str]):
        if isinstance(value, dict):
            nid = value.get("node_id") or node_id
            for key in ("paths", "urls", "images", "frames", "videos"):
                items = value.get(key)
                if isinstance(items, list):
                    for it in items:
                        walk(it, nid)
            for key in ("local_path", "url", "path"):
                if value.get(key):
                    walk(value[key], nid)
            return
        if isinstance(value, list):
            for it in value:
                walk(it, node_id)
            return
        if not isinstance(value, str) or not value:
            return
        if value.startswith(("http://", "https://", "/media/")):
            return
        # 只收 output 目录下的真实文件
        try:
            p = Path(value)
            if not p.is_absolute():
                p = (OUTPUT_DIR.parent / p)
            if not p.exists():
                return
        except Exception:
            return
        kind = _artifact_kind(str(p))
        if not kind:
            return
        url = _to_url(str(p))
        if not url or url in found:
            return
        found[url] = {
            "node_id": node_id or "",
            "kind": kind,
            "url": url,
            "name": p.name,
            "path": str(p),
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        }

    walk(result, None)
    items = sorted(found.values(), key=lambda a: (a["node_id"], a["name"]))
    for it in items:
        it.update(await _media_size(Path(it["path"]), it["kind"]))
    return items


@router.get("/")
async def list_runs(limit: int = 50):
    """内存里的活跃 run + 磁盘上的历史 run（重启后靠后者把列表补回来）。"""
    merged: List[Dict[str, Any]] = []
    seen = set()
    for item in bus.list_runs(limit):
        merged.append(item)
        seen.add(item.get("run_id"))
    for item in RUN_STORE.list_runs(limit):
        if item.get("run_id") in seen:
            continue
        merged.append(item)
        seen.add(item.get("run_id"))
    merged.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return {"runs": merged[:limit], "workflows": _list_workflows()}


@router.get("/workflows")
async def list_workflows():
    return [{"name": n} for n in _list_workflows()]


@router.get("/workflows/{name}")
async def get_workflow(name: str):
    try:
        return workflow_store.read(name)
    except workflow_store.StoreError as exc:
        raise HTTPException(404 if "不存在" in str(exc) else 400, str(exc))


@router.put("/workflows/{name}")
async def save_workflow(name: str, body: Dict[str, Any]):
    """Save node-config edits coming from the canvas UI."""
    try:
        workflow_store.write(name, body)
    except workflow_store.StoreError as exc:
        raise HTTPException(404 if "不存在" in str(exc) else 400, str(exc))
    return {"saved": name}


@router.post("/workflows/{name}")
async def create_workflow(name: str, body: Dict[str, Any] = None):
    """新建工作流。body 可空（给一份单节点骨架），也可直接贴完整 JSON。"""
    doc = body or workflow_store.blank(name)
    try:
        workflow_store.write(name, doc, create=True)
    except workflow_store.StoreError as exc:
        raise HTTPException(400, str(exc))
    return {"created": name, "workflow": workflow_store.read(name)}


@router.delete("/workflows/{name}")
async def delete_workflow(name: str):
    try:
        workflow_store.delete(name)
    except workflow_store.StoreError as exc:
        raise HTTPException(404 if "不存在" in str(exc) else 400, str(exc))
    return {"deleted": name}


@router.get("/meta/node-types")
async def node_types():
    """前端「添加节点」下拉与校验用，避免和引擎分发表脱节。"""
    return {"node_types": workflow_store.NODE_TYPES}


async def _finish(run_id: str, workflow: str, result: dict, input_data: dict) -> None:
    """run 成功后的收尾：成片入库 +（可选）清掉中间产物。

    入库失败不能影响 run 本身的成功状态，所以整体吞异常只记到事件里。
    """
    from .history import record_run

    try:
        record = await record_run(run_id, workflow, result, input_data)
    except Exception as exc:  # noqa: BLE001
        record = None
        bus.publish(run_id, {"type": "node_progress", "node_id": "history",
                             "value": 0, "status": f"入库失败：{exc}"})
    if not record:
        _store_finish(run_id, "completed", result=result)
        bus.finish(run_id, "completed", result=result)
        return

    bus.publish(run_id, {"type": "node_progress", "node_id": "history",
                         "value": 100, "status": f"已入库：{record.get('title') or '成片'}"})

    settings: Dict[str, Any] = {}
    try:
        from .settings import _load_settings

        settings = _load_settings() or {}
    except Exception:
        settings = {}

    if settings.get("auto_cleanup"):
        try:
            from . import system

            stats = await system.cleanup({"dry_run": False})
            bus.publish(run_id, {"type": "node_progress", "node_id": "cleanup",
                                 "value": 100,
                                 "status": f"已清理中间产物 {stats.get('deleted', 0)} 个文件"})
        except Exception as exc:  # noqa: BLE001
            bus.publish(run_id, {"type": "node_progress", "node_id": "cleanup",
                                 "value": 0, "status": f"清理失败：{exc}"})

    _store_finish(run_id, "completed", result=result)
    bus.finish(run_id, "completed", result=result)


def _store_finish(run_id: str, status: str, **kwargs) -> None:
    """落盘收尾。磁盘出问题时不能把 run 本身搞挂，所以一律吞掉。"""
    try:
        RUN_STORE.finish(run_id, status, **kwargs)
    except Exception:  # noqa: BLE001
        pass


def _disk_status(run_id: str) -> Optional[Dict[str, Any]]:
    """内存里没有这个 run（多半是后端重启过）→ 从磁盘恢复一份同样结构的状态。"""
    meta = RUN_STORE.load(run_id)
    if not meta:
        return None
    return {
        "run_id": run_id,
        "status": meta.get("status") or "unknown",
        "workflow": meta.get("workflow") or "",
        "created_at": meta.get("created_at"),
        "finished_at": meta.get("finished_at"),
        "events": 0,
        "result": meta.get("result"),
        "error": meta.get("error"),
        "nodes": meta.get("nodes") or {},
        "from_disk": True,
    }


def _launch(run_id: str, workflow_name: str, config: WorkflowConfig,
            input_data: dict, resume: bool = False) -> None:
    """把 run 挂到后台任务上。

    store 让每个节点/镜头一完成就落盘，所以中途崩掉或后端重启，
    下次可以直接 POST /api/runs/{id}/resume 接着跑没跑完的镜头。
    """
    bus.register(run_id)
    orchestrator = WorkflowOrchestrator(
        config, on_event=lambda ev: bus.publish(run_id, ev),
        store=RUN_STORE, resume=resume,
    )
    input_data = input_data or {}

    async def _run():
        try:
            result = await orchestrator.execute(input_data, run_id=run_id)
            # 关键帧预览门：run 停在等人工确认，状态不能报 completed，
            # 否则状态页会显示「已完成」，但成片其实还没开始做。
            if any(s.status == "waiting_approval" for s in orchestrator.node_states.values()):
                _store_finish(run_id, "waiting_approval", result=result)
                bus.finish(run_id, "waiting_approval", result=result)
                return
            await _finish(run_id, workflow_name, result, input_data)
        except asyncio.CancelledError:
            _store_finish(run_id, "cancelled")
            bus.finish(run_id, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - report everything to the client
            _store_finish(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
            bus.finish(run_id, "failed", error=f"{type(exc).__name__}: {exc}")

    _tasks[run_id] = asyncio.create_task(_run())


@router.post("/")
async def start_run(body: Dict[str, Any]):
    """body: {workflow: "drama-pro", input: {...}, overrides: {node_id: {...}}}

    ``overrides`` 让调用方（一键成片页）按本次需求覆盖节点配置，
    例如把 final_cut 改成横屏、把 narration 的音色换成女声，
    而不用为每种组合存一份工作流 JSON。
    """
    workflow_name = (body.get("workflow") or "").strip()
    if not workflow_name:
        raise HTTPException(400, "缺少 workflow 名称")
    path = WORKFLOW_DIR / f"{workflow_name}.json"
    if not path.exists():
        raise HTTPException(404, f"工作流不存在：{workflow_name}。可用：{_list_workflows()}")

    try:
        config = WorkflowConfig(str(path))
    except Exception as exc:
        raise HTTPException(400, f"工作流加载失败：{exc}")

    overrides = body.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise HTTPException(400, "overrides 必须是 {节点名: {字段: 值}}")
    unknown = [nid for nid in overrides if nid not in config.nodes]
    if unknown:
        raise HTTPException(400, f"overrides 引用了不存在的节点：{unknown}")
    for nid, patch in overrides.items():
        if not isinstance(patch, dict):
            raise HTTPException(400, f"overrides[{nid}] 必须是对象")
        config.nodes[nid].update(patch)

    run_id = uuid.uuid4().hex[:12]
    input_data = body.get("input") or {}
    try:
        RUN_STORE.create(run_id, workflow_name, input_data, overrides,
                         nodes=list(config.nodes.keys()))
    except Exception:  # noqa: BLE001 - 落盘失败不阻断，只是不能续跑
        pass
    _launch(run_id, workflow_name, config, input_data)
    return {"run_id": run_id, "workflow": workflow_name,
            "nodes": list(config.nodes.keys())}


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, body: Dict[str, Any] = None):
    """接着跑上次没跑完的 run（断点续跑）。

    已完成的节点/镜头直接从 checkpoint 复用，只重跑缺失的部分 ——
    30 镜里第 28 镜挂了，不用把前 27 镜重新生成一遍。
    body 可带 {input: {...}} 覆盖输入，{reset: ["node_id"]} 强制重跑某些节点。
    """
    meta = RUN_STORE.load(run_id)
    if not meta:
        raise HTTPException(404, f"没有可续跑的记录：{run_id}")
    workflow_name = meta.get("workflow") or ""
    path = WORKFLOW_DIR / f"{workflow_name}.json"
    if not path.exists():
        raise HTTPException(404, f"记录里的工作流已不存在：{workflow_name}")
    try:
        config = WorkflowConfig(str(path))
    except Exception as exc:
        raise HTTPException(400, f"工作流加载失败：{exc}")

    for nid, patch in (meta.get("overrides") or {}).items():
        if nid in config.nodes and isinstance(patch, dict):
            config.nodes[nid].update(patch)

    body = body or {}
    input_data = body.get("input") or meta.get("input") or {}
    for nid in body.get("reset") or []:
        if nid in config.nodes:
            RUN_STORE.clear_shots(run_id, nid)

    _launch(run_id, workflow_name, config, input_data, resume=True)
    return {"run_id": run_id, "workflow": workflow_name, "resumed": True,
            "nodes": list(config.nodes.keys())}


@router.get("/{run_id}")
async def run_status(run_id: str):
    # 内存里没有就去磁盘找：后端重启后照样能查到上次的结果，不再 404
    status = bus.status(run_id) or _disk_status(run_id)
    if not status:
        raise HTTPException(404, f"run 不存在：{run_id}")
    return status


@router.get("/{run_id}/artifacts")
async def run_artifacts(run_id: str):
    """本次 run 已产出的图片/视频清单（含可直连预览的 URL）。"""
    status = bus.status(run_id) or _disk_status(run_id)
    if not status:
        raise HTTPException(404, f"run 不存在：{run_id}")
    return {"run_id": run_id, "status": status.get("status"),
            "artifacts": await _collect_artifacts(status.get("result"))}


@router.get("/output/recent")
async def recent_outputs(limit: int = 60):
    """按修改时间列出 output/ 下最近的产物，供预览面板兜底展示。"""
    if not OUTPUT_DIR.exists():
        return {"items": []}
    items = []
    for p in OUTPUT_DIR.rglob("*"):
        if not p.is_file():
            continue
        kind = _artifact_kind(str(p))
        if not kind:
            continue
        url = _to_url(str(p))
        if not url:
            continue
        parts = p.relative_to(OUTPUT_DIR).parts
        items.append({
            "node_id": parts[1] if len(parts) > 2 else "",
            "workflow": parts[0] if parts else "",
            "kind": kind,
            "url": url,
            "name": p.name,
            "path": str(p),
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        })
    items.sort(key=lambda a: a["mtime"], reverse=True)
    items = items[:limit]
    for it in items:
        it.update(await _media_size(Path(it["path"]), it["kind"]))
    return {"items": items}


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str):
    task = _tasks.get(run_id)
    if not task or task.done():
        raise HTTPException(404, "run 不存在或已结束")
    task.cancel()
    # 已跑完的镜头留在 checkpoint 里，之后可以 resume 接着跑
    _store_finish(run_id, "cancelled")
    return {"run_id": run_id, "cancelled": True}


@router.get("/{run_id}/events")
async def stream_events(run_id: str, last_seq: int = 0):
    """SSE stream. Replay events after ``last_seq`` first, then live-tail."""
    if not bus.exists(run_id):
        raise HTTPException(404, f"run 不存在：{run_id}")
    try:
        sub_id, queue, replay = bus.subscribe(run_id)
    except KeyError:
        raise HTTPException(404, f"run 不存在：{run_id}")

    replay = [e for e in replay if e.get("seq", 0) > last_seq]

    async def gen():
        try:
            for event in replay:
                yield _sse(event)
            while True:
                if not bus.exists(run_id):
                    break
                status = bus.status(run_id)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event)
                if event.get("type") == "run_end":
                    break
        finally:
            bus.unsubscribe(run_id, sub_id)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _sse(event: dict) -> str:
    import json

    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
