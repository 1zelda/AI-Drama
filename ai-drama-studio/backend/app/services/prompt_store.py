"""提示词模板（config/prompts/scripts/*.yaml）的读写。

这些 yaml 之前只能手改文件，工作流的 llm 节点靠 prompt_template 文件名引用它们。
这里把它变成可列举、可写回的素材，AI 助手才能改「步骤里真正的那段提示词」。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = BACKEND_ROOT / "config" / "prompts" / "scripts"

_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]{0,63}$")


class StoreError(ValueError):
    pass


def safe_key(key: str) -> str:
    key = (key or "").strip()
    if not _NAME_RE.match(key):
        raise StoreError(f"非法模板名：{key!r}（只允许中英文字母、数字、下划线、短横）")
    return key


def path_for(key: str) -> Path:
    return PROMPT_DIR / f"{safe_key(key)}.yaml"


def list_templates() -> List[Dict[str, Any]]:
    if not PROMPT_DIR.exists():
        return []
    out = []
    for p in sorted(PROMPT_DIR.glob("*.yaml")):
        doc = _load(p)
        out.append({
            "key": p.stem,
            "name": doc.get("name", p.stem),
            "type": doc.get("type", "llm"),
            "used_by": _used_by(p.name),
            "placeholders": sorted(set(re.findall(r"\{\{\s*([^{}\s]+)\s*\}\}", doc.get("prompt", "")))),
            "chars": len(doc.get("prompt", "") or ""),
        })
    return out


def read(key: str) -> Dict[str, Any]:
    path = path_for(key)
    if not path.exists():
        raise StoreError(f"提示词模板不存在：{key}")
    return _load(path)


def _load(path: Path) -> Dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise StoreError(f"模板 {path.stem} 不是合法 YAML：{exc}")


def _used_by(filename: str) -> List[str]:
    """哪些工作流节点引用了这个模板（改了会波及谁）。"""
    from .workflow_store import list_workflows, read as read_wf

    used: List[str] = []
    try:
        for wf in list_workflows():
            nodes = (read_wf(wf) or {}).get("nodes") or {}
            for nid, cfg in nodes.items():
                if isinstance(cfg, dict) and str(cfg.get("prompt_template", "")) == filename:
                    used.append(f"{wf}/{nid}")
    except StoreError:
        return []
    return used


def validate(key: str, doc: Any) -> None:
    if not isinstance(doc, dict):
        raise StoreError("模板必须是一个 YAML 映射")
    prompt = doc.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise StoreError("模板缺少非空的 prompt 字段")
    config = doc.get("config", {})
    if config is not None and not isinstance(config, dict):
        raise StoreError("config 必须是映射（model / temperature / max_tokens）")


def _comments_by_key(text: str) -> Dict[str, List[str]]:
    """原文件里每个顶层键前面挂着的注释块。

    plot.yaml 那些「写作铁律」「输出给谁用」的注释写在键与键之间，
    safe_dump 会把它们整段丢掉 —— 改一次提示词就删掉一份文档，不能接受。
    """
    out: Dict[str, List[str]] = {}
    pending: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            pending.append(line)
            continue
        if not stripped:
            continue
        # 只认顶格的键（块标量里的内容都是缩进的，不会被误判）
        m = re.match(r"^([A-Za-z_][\w\-]*):", line)
        if m:
            out[m.group(1)] = pending
            pending = []
    return out


def _reattach_comments(path: Path, body: str) -> str:
    if not path.exists():
        return body
    try:
        comments = _comments_by_key(path.read_text(encoding="utf-8"))
    except OSError:
        return body
    if not comments:
        return body

    lines: List[str] = []
    for line in body.splitlines():
        m = re.match(r"^([A-Za-z_][\w\-]*):", line)
        if m and comments.get(m.group(1)):
            if lines and lines[-1] != "":
                lines.append("")
            lines.extend(comments[m.group(1)])
        lines.append(line)
    return "\n".join(lines).lstrip("\n") + "\n"


def write(key: str, doc: Any, *, create: bool = False) -> Dict[str, Any]:
    key = safe_key(key)
    path = path_for(key)
    if create and path.exists():
        raise StoreError(f"模板已存在：{key}")
    validate(key, doc)
    doc = dict(doc)
    doc.setdefault("name", key)
    doc.setdefault("type", "llm")

    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=1000)
    tmp = path.with_suffix(".yaml.write")
    tmp.write_text(_reattach_comments(path, body), encoding="utf-8")
    tmp.replace(path)
    return doc


def delete(key: str) -> None:
    path = path_for(key)
    if not path.exists():
        raise StoreError(f"提示词模板不存在：{key}")
    used = _used_by(path.name)
    if used:
        raise StoreError(f"模板仍被节点引用，不能删除：{used}")
    path.unlink()


def blank(key: str, description: str = "") -> Dict[str, Any]:
    return {
        "name": safe_key(key),
        "type": "llm",
        "description": description,
        "prompt": "你是资深短剧编剧。\n\n主题：{{title}}\n\n严格输出 JSON。",
        "config": {"model": "deepseek-chat", "temperature": 0.8, "max_tokens": 2500},
    }
