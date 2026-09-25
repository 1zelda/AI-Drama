"""镜头路由：shot_type → 供应商选择 + 提示词模板 + 水印预设。

用法（引擎/工作流节点）：
    节点 config 给 "shot_type": "character_closeup"，不给 provider 时自动路由；
    provider 显式给出时以显式为准。运动提示词可用 fill_motion_template() 套模板。
配置文件：backend/config/shot_routing.json（可被 UI 修改）。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "shot_routing.json"
_lock = threading.Lock()


def load_routing() -> Dict[str, Any]:
    with _lock:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_routing(data: Dict[str, Any]):
    with _lock:
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_shot_type(shot_type: Optional[str]) -> Optional[Dict[str, Any]]:
    if not shot_type:
        return None
    for st in load_routing().get("shot_types", []):
        if st.get("id") == shot_type:
            return st
    return None


def route_provider(shot_type: Optional[str], explicit_provider: Optional[str] = None,
                   preferred_fallback_order: bool = False,
                   allowed: Optional[set] = None) -> Dict[str, Any]:
    """返回 {provider, shot_type_info, source}。显式 provider 优先；否则按 preferred，再 fallback。

    ``allowed`` 是当前模态（生图/生视频）实际支持的 provider 名集合。
    路由表里的 preferred 是跨模态写的（比如 scene 首选 agnes，那是视频专用），
    不加这道过滤，生图节点会被派到不存在的 provider 直接抛 ValueError。
    """
    info = resolve_shot_type(shot_type)
    if not info:
        return {"provider": explicit_provider, "source": "explicit_or_default", "shot_type": None}
    if explicit_provider:
        return {"provider": explicit_provider, "source": "explicit", "shot_type": info}

    candidates = [info.get("preferred"), info.get("fallback")]
    if allowed is not None:
        candidates = [c for c in candidates if c and c in allowed]
    provider = candidates[0] if candidates else None
    if not provider:
        return {"provider": None, "source": "default", "shot_type": info}
    source = "preferred" if provider == info.get("preferred") else "fallback"
    return {"provider": provider, "source": source, "shot_type": info}


def check_banned(shot_type: Optional[str], provider: str) -> Optional[str]:
    """供应商被该镜头类型禁用则返回拒绝理由。"""
    info = resolve_shot_type(shot_type)
    if info and provider in (info.get("banned") or []):
        return (
            f"镜头类型 {shot_type!r} 禁用 provider {provider!r}（{info.get('notes', '')}）。"
            f"该类型应使用 {info.get('preferred')!r}（备选 {info.get('fallback')!r}）。"
        )
    return None


def fill_motion_template(shot_type: Optional[str], micro_action: Optional[str] = None,
                         user_motion: Optional[str] = None) -> Optional[str]:
    """按镜头类型套运动提示词模板；用户给了显式运动描述时原样尊重。"""
    if user_motion:
        return user_motion
    info = resolve_shot_type(shot_type)
    if not info:
        return user_motion
    template = info.get("motion_template")
    if not template:
        return None
    actions = info.get("micro_actions") or []
    action = micro_action or (actions[0] if actions else "细微呼吸起伏")
    return template.format(micro_action=action, action=action, element=action, effect=action)


def watermark_preset_for(provider: str, shot_type: Optional[str] = None) -> Optional[str]:
    """该供应商产物需要去水印时返回预设名。shot_type 可覆盖默认。"""
    from ..providers.watermark import WATERMARK_PRESETS
    if provider not in WATERMARK_PRESETS:
        return None
    info = resolve_shot_type(shot_type)
    if info and info.get("watermark"):
        return info["watermark"] if info["watermark"] in WATERMARK_PRESETS else None
    return provider
