"""工作流 JSON（config/workflows/*.json）的读写与校验。

画布保存和 AI 助手写回共用这一套：落盘前先用引擎自己的 WorkflowConfig 过一遍，
改坏的结构不会污染线上文件。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

BACKEND_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = BACKEND_ROOT / "config" / "workflows"

# 与 engine._execute_node 的分发表保持一致；写回时拦住笔误，
# 否则要等到真正跑到那个节点才炸出「未知节点类型」。
NODE_TYPES = [
    "llm", "text", "image", "comfyui_image", "comfyui",
    "video", "comfyui_video", "agnes_video", "tts",
    "video_input", "ffmpeg", "postprocess", "noop",
]

_NAME_RE = re.compile(r"^[\w\u4e00-\u9fa5][\w\u4e00-\u9fa5.\- ]{0,79}$")


class StoreError(ValueError):
    """校验/定位失败。API 层统一转 400。"""


def safe_name(name: str) -> str:
    name = (name or "").strip()
    if not name or not _NAME_RE.match(name):
        raise StoreError(f"非法工作流名：{name!r}（只允许中英文、数字、下划线、点、短横、空格）")
    return name


def path_for(name: str) -> Path:
    return WORKFLOW_DIR / f"{safe_name(name)}.json"


def list_workflows() -> List[str]:
    if not WORKFLOW_DIR.exists():
        return []
    return sorted(p.stem for p in WORKFLOW_DIR.glob("*.json") if not p.name.startswith("_"))


def read(name: str) -> Dict[str, Any]:
    path = path_for(name)
    if not path.exists():
        raise StoreError(f"工作流不存在：{name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StoreError(f"工作流 {name} 不是合法 JSON：{exc}")


def validate(name: str, doc: Any) -> None:
    if not isinstance(doc, dict):
        raise StoreError("工作流必须是一个 JSON 对象")
    nodes = doc.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise StoreError("工作流缺少 nodes（至少一个节点）")
    for nid, cfg in nodes.items():
        if not _NAME_RE.match(str(nid)):
            raise StoreError(f"非法节点名：{nid!r}")
        if not isinstance(cfg, dict):
            raise StoreError(f"节点 {nid} 的配置必须是对象")
        ntype = str(cfg.get("type", "")).lower()
        if not ntype:
            raise StoreError(f"节点 {nid} 缺少 type")
        if ntype not in NODE_TYPES:
            raise StoreError(f"节点 {nid} 的 type={ntype!r} 不支持，可用：{NODE_TYPES}")
        deps = cfg.get("depends_on", [])
        if not isinstance(deps, list) or any(d not in nodes for d in deps):
            raise StoreError(f"节点 {nid} 的 depends_on 引用了不存在的节点：{deps}")

    # 循环依赖交给引擎自己判：写临时文件后走一遍 WorkflowConfig
    from ..orchestrator import WorkflowConfig

    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    tmp = WORKFLOW_DIR / f".validate-{name}.write"
    try:
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        WorkflowConfig(str(tmp)).get_execution_order()
    except StoreError:
        raise
    except Exception as exc:  # noqa: BLE001 - 引擎的报错统一换成 API 能直接展示的 StoreError
        raise StoreError(f"工作流结构不合法：{exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)


def write(name: str, doc: Any, *, create: bool = False) -> Dict[str, Any]:
    """校验通过后原子落盘。create=True 时拒绝覆盖已有工作流。"""
    name = safe_name(name)
    path = path_for(name)
    if create and path.exists():
        raise StoreError(f"工作流已存在：{name}")
    doc = dict(doc)
    doc.setdefault("name", name)
    validate(name, doc)

    tmp = path.with_suffix(".json.write")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return doc


def delete(name: str) -> None:
    path = path_for(name)
    if not path.exists():
        raise StoreError(f"工作流不存在：{name}")
    path.unlink()


def blank(name: str, description: str = "") -> Dict[str, Any]:
    return {
        "name": safe_name(name),
        "description": description,
        "version": "1.0.0",
        "execution": {"max_retries": 2, "timeout": 600},
        "nodes": {
            "script": {
                "type": "llm",
                "prompt_template": "plot.yaml",
                "depends_on": [],
                "output": "story",
            }
        },
    }
