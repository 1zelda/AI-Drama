"""ComfyUI integration (OpenMontage pattern, extended).

Handles: preflight -> submit workflow -> wait (ws, with history fallback) -> download.

Design notes
------------
* **Capability routing.** One logical pipeline may want images rendered on one
  box and video on another (e.g. a 16 GB card does SDXL fine but Wan 2.2 I2V
  wants its own machine). Pass ``capability="image"`` / ``"video"`` and the
  client resolves ``COMFYUI_IMAGE_SERVER_URL`` / ``COMFYUI_VIDEO_SERVER_URL``
  first, then ``COMFYUI_SERVER_URL``, then localhost.

* **History is authoritative.** Websocket events are not replayed, so a job can
  finish between "submit returned" and "ws connected". We probe ``/history``
  before opening the socket and again after the socket gives up, then fall back
  to plain polling. Without this you get phantom timeouts on fast jobs.

* **Resumable waits.** ``generate(..., resume_prompt_id=...)`` re-attaches to an
  already queued prompt instead of re-submitting. Re-submitting a 14 B video job
  because the client timed out is an expensive mistake.

* **Type coercion.** Workflow JSON carries ``{{placeholders}}`` that render to
  strings. ComfyUI rejects a string ``seed``. Patches are coerced to the type of
  the value they replace, and ``/object_info`` is consulted when available.
"""
from __future__ import annotations

import copy
import json
import mimetypes
import os
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

try:
    import websocket  # websocket-client

    HAS_WS = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_WS = False


class ComfyUIError(Exception):
    """Raised for any ComfyUI failure. Carries ``prompt_id`` so callers can resume."""

    def __init__(self, message: str, prompt_id: Optional[str] = None, detail: Any = None):
        super().__init__(message)
        self.prompt_id = prompt_id
        self.detail = detail


# Wrapped workflow files look like {name, description, type, output_node, nodes:{...}}.
# ComfyUI's /prompt endpoint wants the bare node graph, so we unwrap on load.
_WRAPPER_KEYS = {"name", "description", "type", "output_node", "params"}


class ComfyUIClient:
    """REST + WebSocket client for a ComfyUI server (local or remote)."""

    def __init__(self, server_url: Optional[str] = None, capability: Optional[str] = None,
                 *, object_info_ttl: float = 300.0):
        self.capability = capability.lower() if capability else None
        self._env_var = f"COMFYUI_{self.capability.upper()}_SERVER_URL" if self.capability else None
        resolved = (
            server_url
            or (os.environ.get(self._env_var) if self._env_var else None)
            or os.environ.get("COMFYUI_SERVER_URL")
            or "http://localhost:8188"
        )
        self.server_url = resolved.rstrip("/")
        self.client_id = str(uuid.uuid4())
        self._object_info_ttl = object_info_ttl
        self._object_info_cache: Optional[dict] = None
        self._object_info_at = 0.0

    def _cap_url(self) -> Optional[str]:
        return os.environ.get(self._env_var) if self._env_var else None

    # ------------------------------------------------------------------ #
    # Health / introspection
    # ------------------------------------------------------------------ #

    def is_available(self, timeout: float = 5.0) -> bool:
        try:
            r = requests.get(f"{self.server_url}/system_stats", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    def system_stats(self) -> Dict[str, Any]:
        """VRAM/device info, so the UI can show *why* a workflow will or won't fit."""
        try:
            r = requests.get(f"{self.server_url}/system_stats", timeout=8)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    def object_info(self, node_class: Optional[str] = None, *, refresh: bool = False) -> dict:
        """``/object_info`` — the authoritative list of node types and their input types."""
        now = time.time()
        if (
            refresh
            or self._object_info_cache is None
            or (now - self._object_info_at) > self._object_info_ttl
        ):
            try:
                r = requests.get(f"{self.server_url}/object_info", timeout=30)
                r.raise_for_status()
                self._object_info_cache = r.json()
                self._object_info_at = now
            except Exception:
                return {} if node_class is None else {}
        info = self._object_info_cache or {}
        if node_class:
            return info.get(node_class) or {}
        return info

    def has_node(self, node_class: str) -> bool:
        """True if the server knows this node type (i.e. the custom node is installed)."""
        return bool(self.object_info(node_class))

    def list_models(self) -> Dict[str, List[str]]:
        try:
            r = requests.get(f"{self.server_url}/models", timeout=15)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "checkpoints" in data:
                return data
            # Some builds return a bare list of filenames.
            return {"checkpoints": list(data)} if isinstance(data, list) else {}
        except Exception:
            return {"checkpoints": [], "loras": [], "vae": [], "clip": [], "unet": []}

    def check_models(self, required: List[str]) -> Tuple[List[str], List[str]]:
        """Return ``(missing, present)`` for a list of model filenames.

        Matches on basename so ``models/unet/foo.safetensors`` and ``foo.safetensors``
        compare equal. This is what lets us fail *before* burning a queue slot.
        """
        available: set[str] = set()
        for group in self.list_models().values():
            if isinstance(group, list):
                for item in group:
                    available.add(Path(str(item)).name)
        missing, present = [], []
        for req in required:
            name = Path(str(req)).name
            (present if name in available else missing).append(req)
        return missing, present

    def queue_status(self) -> Dict[str, Any]:
        try:
            r = requests.get(f"{self.server_url}/queue", timeout=8)
            r.raise_for_status()
            data = r.json()
            return {
                "running": len(data.get("queue_running") or []),
                "pending": len(data.get("queue_pending") or []),
            }
        except Exception:
            return {"running": 0, "pending": 0}

    # ------------------------------------------------------------------ #
    # Workflow loading / patching
    # ------------------------------------------------------------------ #

    @staticmethod
    def load_workflow(path) -> Dict[str, Any]:
        """Load a workflow file and return the **bare node graph**.

        Accepts both the wrapped format (``{"name":..., "nodes": {...}}``) used by
        ``config/comfyui_workflows/`` and a raw API-format graph. Returning the
        wrapper here is what broke ``patch_workflow`` and ``/prompt`` before.
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "nodes" in data and set(data) <= _WRAPPER_KEYS | {"nodes"}:
            graph = data.get("nodes") or {}
            if not isinstance(graph, dict):
                raise ComfyUIError(f"Workflow {path}: 'nodes' must be an object keyed by node id")
            return graph
        return data

    @staticmethod
    def load_workflow_meta(path) -> Dict[str, Any]:
        """Load the wrapper metadata (name/description/type/output_node) if present."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {k: data.get(k) for k in ("name", "description", "type", "output_node", "params")}

    @staticmethod
    def _coerce(old: Any, new: Any) -> Any:
        """Coerce ``new`` toward the type of ``old``.

        Fixes the classic ``"seed": "{{seed}}"`` -> ``"seed": "12345"`` rejection:
        ComfyUI's validator wants an int (or a float for weight/strength inputs).
        """
        if old is None or new is None:
            return new
        if isinstance(old, bool) or isinstance(new, bool):
            if isinstance(new, str):
                return new.strip().lower() in ("1", "true", "yes", "on")
            return bool(new)
        if isinstance(old, int) and not isinstance(old, bool):
            try:
                return int(float(new)) if isinstance(new, str) else int(new)
            except (TypeError, ValueError):
                return new
        if isinstance(old, float):
            try:
                return float(new)
            except (TypeError, ValueError):
                return new
        return new

    @classmethod
    def patch_workflow(cls, workflow: dict, patches: Dict[str, Dict[str, Any]],
                       *, strict: bool = True, coerce: bool = True) -> dict:
        """Deep-copy ``workflow`` and apply ``{node_id: {input_name: value}}`` patches.

        ``strict=False`` silently skips unknown node ids, which is what you want when
        one template is reused across several models with slightly different graphs.
        """
        w = copy.deepcopy(workflow)
        for nid, vals in (patches or {}).items():
            node = w.get(str(nid))
            if node is None:
                if strict:
                    raise ComfyUIError(
                        f"Node {nid!r} not found in workflow. Available: {sorted(w)}"
                    )
                continue
            inputs = node.setdefault("inputs", {})
            for k, v in vals.items():
                if coerce and k in inputs:
                    v = cls._coerce(inputs[k], v)
                inputs[k] = v
        return w

    @staticmethod
    def scan_placeholders(workflow: dict) -> List[Dict[str, str]]:
        """Find every ``{{name}}`` placeholder so the UI can auto-build a form."""
        found: Dict[str, Dict[str, str]] = {}

        def walk(node_id: str, value: Any, field: str):
            if isinstance(value, str):
                start = 0
                while True:
                    i = value.find("{{", start)
                    if i < 0:
                        return
                    j = value.find("}}", i + 2)
                    if j < 0:
                        return
                    key = value[i + 2:j].strip()
                    if key:
                        found.setdefault(key, {"name": key, "node": node_id, "field": field})
                    start = j + 2
            elif isinstance(value, list):
                for item in value:
                    walk(node_id, item, field)

        for nid, node in (workflow or {}).items():
            if not isinstance(node, dict):
                continue
            for field, value in (node.get("inputs") or {}).items():
                walk(nid, value, field)
        return sorted(found.values(), key=lambda d: d["name"])

    def preflight(self, workflow: dict) -> Dict[str, Any]:
        """Cheap sanity check before queuing: unknown node types + missing models.

        ComfyUI will reject the whole prompt for a single typo'd node class, and
        a missing checkpoint only surfaces after the queue slot is taken.
        """
        info = self.object_info()
        if not info:
            return {"ok": False, "reason": "unreachable",
                    "message": f"无法连接 ComfyUI（{self.server_url}），无法预检。"}

        unknown_nodes = []
        model_files: List[str] = []
        model_fields = {
            "ckpt_name", "unet_name", "vae_name", "clip_name", "lora_name",
            "clip_name1", "clip_name2", "image", "model_path",
        }
        for nid, node in (workflow or {}).items():
            if not isinstance(node, dict):
                continue
            cls_name = node.get("class_type")
            if cls_name and cls_name not in info:
                unknown_nodes.append({"node": nid, "class_type": cls_name})
            for field, value in (node.get("inputs") or {}).items():
                # Only filenames that look like weights; "image" may be a placeholder.
                if field in model_fields and isinstance(value, str) and not (
                    value.startswith("{{") or value.endswith((".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"))
                ):
                    model_files.append(value)

        missing, present = self.check_models(sorted(set(model_files)))
        return {
            "ok": not unknown_nodes and not missing,
            "server_url": self.server_url,
            "unknown_nodes": unknown_nodes,
            "missing_models": missing,
            "present_models": present,
            "queue": self.queue_status(),
        }

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    def submit(self, workflow: dict) -> str:
        """Queue a workflow and return ``prompt_id``."""
        w = copy.deepcopy(workflow)
        w.pop("client_id", None)
        try:
            r = requests.post(
                f"{self.server_url}/prompt",
                json={"prompt": w, "client_id": self.client_id},
                timeout=60,
            )
        except Exception as exc:
            raise ComfyUIError(f"提交工作流失败（{self.server_url}）：{exc}") from exc

        if r.status_code != 200:
            raise ComfyUIError(
                f"ComfyUI 拒绝工作流（HTTP {r.status_code}）: {r.text[:600]}", detail=r.text
            )

        payload = r.json()
        if payload.get("node_errors"):
            raise ComfyUIError(
                "ComfyUI 节点校验失败: "
                + json.dumps(payload["node_errors"], ensure_ascii=False)[:800],
                detail=payload.get("node_errors"),
            )
        return payload["prompt_id"]

    def history(self, prompt_id: str) -> Optional[dict]:
        try:
            r = requests.get(f"{self.server_url}/history/{prompt_id}", timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception:
            return None
        return data.get(prompt_id) if isinstance(data, dict) else None

    def poll(self, prompt_id: str, timeout: float = 600, interval: float = 3.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            entry = self.history(prompt_id)
            if entry:
                return entry
            time.sleep(interval)
        raise ComfyUIError(f"等待任务超时（{timeout}s）: {prompt_id}", prompt_id)

    def _wait_ws(self, prompt_id: str, timeout: float = 600,
                 on_progress: Optional[Callable[[int, int, str], None]] = None) -> Optional[dict]:
        """Wait via websocket. Returns the history entry, or None if it timed out."""
        if not HAS_WS:
            return None

        result: Dict[str, Any] = {"entry": None, "done": False, "error": None}
        lock = threading.Lock()

        def _finish(entry):
            with lock:
                if result["entry"] is None:
                    result["entry"] = entry
                result["done"] = True

        def _on_message(_, raw):
            try:
                msg = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                return
            if not isinstance(msg, dict):
                return
            mtype = msg.get("type")
            data = msg.get("data") or {}
            if mtype == "executing":
                # node is None -> the whole prompt finished
                if data.get("node") is None and data.get("prompt_id") == prompt_id:
                    _finish(self.history(prompt_id))
            elif mtype == "progress" and on_progress:
                try:
                    on_progress(int(data.get("value", 0)), int(data.get("max", 1)), str(data.get("node", "")))
                except Exception:
                    pass
            elif mtype == "executed" and data.get("prompt_id") == prompt_id:
                _finish(self.history(prompt_id))
            elif mtype == "execution_error":
                result["error"] = data
                _finish(self.history(prompt_id))

        def _on_error(_, err):
            result["error"] = str(err)
            result["done"] = True

        ws_url = f"{self.server_url.replace('http', 'ws', 1)}/ws?clientId={self.client_id}"
        ws = websocket.WebSocketApp(ws_url, on_message=_on_message, on_error=_on_error)
        thread = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 20, "ping_timeout": 10})
        thread.daemon = True
        thread.start()

        deadline = time.time() + timeout
        try:
            while not result["done"] and time.time() < deadline:
                time.sleep(0.15)
        finally:
            try:
                ws.close()
            except Exception:
                pass
            thread.join(timeout=3)

        if result["error"] and not result["entry"]:
            raise ComfyUIError(
                f"ComfyUI 执行出错: {json.dumps(result['error'], ensure_ascii=False)[:600]}",
                prompt_id, detail=result["error"],
            )
        return result["entry"]

    def wait(self, prompt_id: str, timeout: float = 600,
             on_progress: Optional[Callable[[int, int, str], None]] = None) -> dict:
        """Wait for a queued prompt using history -> websocket -> history -> poll.

        The leading history probe catches jobs that completed before we connected;
        the trailing one catches jobs that completed while the socket was flaky.
        """
        entry = self.history(prompt_id)
        if entry:
            return entry

        if HAS_WS:
            try:
                entry = self._wait_ws(prompt_id, timeout=timeout, on_progress=on_progress)
            except ComfyUIError:
                raise
            except Exception:
                entry = None
            if entry:
                return entry

        entry = self.history(prompt_id)
        if entry:
            return entry
        return self.poll(prompt_id, timeout=timeout)

    def download(self, filename: str, subfolder: str, dest: Path, folder_type: str = "output") -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(
            f"{self.server_url}/view",
            params={"filename": filename, "subfolder": subfolder, "type": folder_type},
            timeout=300,
        )
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest

    def generate(self, workflow: dict, output_node: str, dest, *, timeout: float = 900,
                 resume_prompt_id: Optional[str] = None,
                 on_progress: Optional[Callable[[int, int, str], None]] = None,
                 preflight: bool = False) -> List[Path]:
        """Submit -> wait -> download. Returns artifact paths.

        Pass ``resume_prompt_id`` to re-attach to a job that already started; this
        avoids re-queuing (and re-paying for) a job we merely lost track of.
        """
        dest = Path(dest)
        if preflight and not resume_prompt_id:
            check = self.preflight(workflow)
            if not check["ok"]:
                raise ComfyUIError(
                    "ComfyUI 预检未通过: "
                    + json.dumps({k: check[k] for k in ("unknown_nodes", "missing_models")},
                                 ensure_ascii=False)[:800],
                    detail=check,
                )

        prompt_id = resume_prompt_id or self.submit(workflow)
        entry = self.wait(prompt_id, timeout=timeout, on_progress=on_progress)

        outputs = entry.get("outputs") or {}
        node_out = outputs.get(str(output_node)) or {}
        items: List[dict] = []
        for key in ("images", "gifs", "video", "videos", "audio", "files"):
            value = node_out.get(key)
            if isinstance(value, list) and value:
                items = value
                break
        if not items:
            # Fall back to any node that produced something, so a renamed output
            # node degrades to "wrong file" instead of "no file".
            for _nid, out in outputs.items():
                for key in ("images", "gifs", "video", "videos"):
                    if isinstance(out.get(key), list) and out[key]:
                        items = out[key]
                        break
                if items:
                    break
        if not items:
            raise ComfyUIError(f"节点 {output_node} 没有产出任何文件", prompt_id, detail=outputs)

        paths: List[Path] = []
        for i, item in enumerate(items):
            filename = item.get("filename")
            if not filename:
                continue
            suffix = Path(filename).suffix or ".png"
            target = dest if len(items) == 1 else dest.with_name(f"{dest.stem}_{i:03d}{suffix}")
            self.download(
                filename,
                item.get("subfolder", ""),
                target,
                item.get("type", "output"),
            )
            paths.append(target)
        return paths

    # ------------------------------------------------------------------ #
    # Uploads (reference images / videos / masks)
    # ------------------------------------------------------------------ #

    def upload_image(self, local_path, name: Optional[str] = None, *,
                     subfolder: str = "", overwrite: bool = True) -> str:
        """Upload a local file to ComfyUI's input dir so LoadImage can reference it.

        Returns the name ComfyUI assigned (which may differ from ``name`` when
        ``overwrite`` is False and the file already exists).
        """
        local_path = Path(local_path)
        name = name or local_path.name
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        data = {"overwrite": "true" if overwrite else "false"}
        if subfolder:
            data["subfolder"] = subfolder
        with open(local_path, "rb") as f:
            r = requests.post(
                f"{self.server_url}/upload/image",
                files={"image": (name, f, ctype)},
                data=data,
                timeout=120,
            )
        try:
            r.raise_for_status()
        except Exception as exc:
            raise ComfyUIError(f"上传参考图失败 {name}: {exc} / {r.text[:300]}") from exc
        return r.json().get("name", name)

    @staticmethod
    def random_seed() -> int:
        return random.randint(0, 2**32 - 1)


def get_comfy_client(capability: Optional[str] = None, server_url: Optional[str] = None) -> ComfyUIClient:
    return ComfyUIClient(server_url=server_url, capability=capability)
