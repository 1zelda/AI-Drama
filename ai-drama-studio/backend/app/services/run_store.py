"""Run 状态落盘 —— 后端重启后 run 不再 404，并支持断点续跑。

以前所有 run 状态只活在 EventBus 的内存里：后端一重启（或被系统回收）
``/api/runs/{id}`` 立刻 404。一集 30 镜要跑 40 分钟，跑完必须马上抓结果，
晚一步就白跑。这里把状态增量写盘：

    data/runs/<run_id>.json                    run 元信息 + 各节点状态/输出
    data/runs/<run_id>/shots/<node>/<i>.json   镜头级 checkpoint（断点续跑用）

写盘一律走「临时文件 + os.replace」，进程被杀时不会留下半个坏 JSON。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_META_SUFFIX = ".json"


def _jsonable(value: Any) -> Any:
    """把 run 输出收敛成能进 JSON 的东西（Path/datetime 之类一律转字符串）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


class RunStore:
    """一次 run 一份 JSON，节点完成即增量落盘。"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base = (
            Path(base_dir)
            if base_dir
            else Path(__file__).parent.parent.parent / "data" / "runs"
        )
        self.base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # paths
    # ------------------------------------------------------------------ #

    def _meta_path(self, run_id: str) -> Path:
        return self.base / f"{run_id}{_META_SUFFIX}"

    def _shots_dir(self, run_id: str, node_id: str) -> Path:
        return self.base / str(run_id) / "shots" / str(node_id)

    # ------------------------------------------------------------------ #
    # meta
    # ------------------------------------------------------------------ #

    def create(self, run_id: str, workflow: str, input_data: Any = None,
               overrides: Any = None, nodes: Optional[List[str]] = None) -> dict:
        now = datetime.now()
        meta = {
            "run_id": run_id,
            "workflow": workflow,
            "input": _jsonable(input_data or {}),
            "overrides": _jsonable(overrides or {}),
            "nodes_order": list(nodes or []),
            "nodes": {},
            "status": "running",
            "error": None,
            "result": None,
            "created_at": time.time(),
            "updated_at": time.time(),
            "created_at_iso": now.isoformat(),
            "updated_at_iso": now.isoformat(),
            "finished_at": None,
            "finished_at_iso": None,
        }
        self._write(run_id, meta)
        return meta

    def load(self, run_id: str) -> Optional[dict]:
        path = self._meta_path(run_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write(self, run_id: str, meta: dict) -> None:
        meta["updated_at"] = time.time()
        meta["updated_at_iso"] = datetime.now().isoformat()
        path = self._meta_path(run_id)
        tmp = path.with_suffix(".json.tmp")
        with self._lock:
            tmp.write_text(json.dumps(_jsonable(meta), ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(str(tmp), str(path))

    def patch(self, run_id: str, **fields) -> Optional[dict]:
        meta = self.load(run_id)
        if meta is None:
            return None
        meta.update(fields)
        self._write(run_id, meta)
        return meta

    def finish(self, run_id: str, status: str, result: Any = None,
               error: Optional[str] = None) -> Optional[dict]:
        meta = self.load(run_id)
        if meta is None:
            return None
        now = datetime.now()
        meta["status"] = status
        meta["finished_at"] = time.time()
        meta["finished_at_iso"] = now.isoformat()
        if result is not None:
            meta["result"] = _jsonable(result)
        if error is not None:
            meta["error"] = str(error)
        self._write(run_id, meta)
        return meta

    # ------------------------------------------------------------------ #
    # node-level state
    # ------------------------------------------------------------------ #

    def save_node(self, run_id: str, node_id: str, state: dict) -> None:
        meta = self.load(run_id)
        if meta is None:
            return
        meta.setdefault("nodes", {})[node_id] = _jsonable(state)
        self._write(run_id, meta)

    # ------------------------------------------------------------------ #
    # shot-level checkpoint（断点续跑）
    # ------------------------------------------------------------------ #

    def save_shot(self, run_id: str, node_id: str, index: int, payload: Any) -> None:
        """记录第 index 个 foreach 项的产物。一个镜头一个文件，写完就可 resume。"""
        try:
            d = self._shots_dir(run_id, node_id)
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f"{index:04d}.json.tmp"
            tmp.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(str(tmp), str(d / f"{index:04d}.json"))
        except OSError:
            pass

    def load_shots(self, run_id: str, node_id: str) -> Dict[int, Any]:
        d = self._shots_dir(run_id, node_id)
        if not d.exists():
            return {}
        out: Dict[int, Any] = {}
        for p in d.glob("*.json"):
            try:
                out[int(p.stem)] = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
        return out

    def clear_shots(self, run_id: str, node_id: Optional[str] = None) -> int:
        """重跑前清掉 checkpoint。不传 node_id 就清整条 run。"""
        root = self.base / str(run_id) / "shots"
        target = root / str(node_id) if node_id else root
        if not target.exists():
            return 0
        n = 0
        for p in target.rglob("*.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                continue
        return n

    # ------------------------------------------------------------------ #
    # listing / cleanup
    # ------------------------------------------------------------------ #

    def list_runs(self, limit: int = 50) -> List[dict]:
        items: List[dict] = []
        for p in self.base.glob(f"*{_META_SUFFIX}"):
            try:
                meta = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            items.append({
                "run_id": meta.get("run_id") or p.stem,
                "workflow": meta.get("workflow") or "",
                "status": meta.get("status") or "unknown",
                "created_at": meta.get("created_at") or p.stat().st_mtime,
                "created_at_iso": meta.get("created_at_iso"),
                "finished_at": meta.get("finished_at"),
                "from_disk": True,
            })
        items.sort(key=lambda m: m.get("created_at") or 0, reverse=True)
        return items[:limit]

    def delete(self, run_id: str) -> bool:
        removed = False
        meta = self._meta_path(run_id)
        if meta.exists():
            try:
                meta.unlink()
                removed = True
            except OSError:
                pass
        import shutil

        d = self.base / str(run_id)
        if d.exists():
            shutil.rmtree(str(d), ignore_errors=True)
            removed = True
        return removed
