"""自动选择可用通道。

以前每个节点的 provider 要么写死在工作流 JSON 里，要么靠 shot_routing 的
preferred 字段 —— 一旦那个 Key 没配，节点就直接抛错，用户得去改 JSON 才知道
为什么跑不起来。这里做一层兜底：按「免费优先 + 已配置优先」的顺序，
自动挑一个当前真能用的 provider。

同时给设置页的「一键自检」提供数据来源。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

SETTINGS_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "data" / "settings.json"
)

# settings.json 字段 → 环境变量（后端各处读的是环境变量）
_KEY_TO_ENV = {
    "api_key": "OPENAI_API_KEY",
    "base_url": "OPENAI_BASE_URL",
    "model": "LLM_MODEL",
    "comfyui_url": "COMFYUI_SERVER_URL",
    "agnes_api_key": "AGNES_API_KEY",
    "agnes_proxy_url": "AGNES_PROXY_URL",
    "agnes_video_model": "AGNES_VIDEO_MODEL",
    "modelscope_token": "MODELSCOPE_TOKEN",
    "siliconflow_api_key": "SILICONFLOW_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "zhipu_api_key": "ZHIPU_API_KEY",
    "kling_api_key": "KLING_API_KEY",
    "seedance_api_key": "ARK_API_KEY",
    "veo_api_key": "VEO_API_KEY",
    "dashscope_api_key": "DASHSCOPE_API_KEY",
    # 下面三项不是「某个供应商的 Key」，但同样只有环境变量被读：
    # 视觉反推（魔改线看关键帧）、本地文件发布成公网 URL、gptsovits 配音。
    "vision_api_key": "VISION_API_KEY",
    "vision_base_url": "VISION_BASE_URL",
    "vision_model": "VISION_MODEL",
    "public_asset_base_url": "PUBLIC_ASSET_BASE_URL",
    "gptsovits_url": "GPTSOVITS_URL",
}

# 免费优先，其次才是付费/需部署
IMAGE_PREFERENCE = ["zhipu", "modelscope", "siliconflow", "pollinations", "http", "comfyui"]
# dashscope 排在最后：它是付费通道，自动挑通道时永远不该抢免费位的活；
# 但节点上显式写 provider=dashscope（魔改线的 video_edit）时能正常认出来。
VIDEO_PREFERENCE = ["zhipu", "modelscope", "seedance", "agnes", "kling", "comfyui", "dashscope"]

# provider → 判断「已配置」所需的 settings 字段 / 环境变量
_REQUIREMENT = {
    "zhipu": ("zhipu_api_key", "ZHIPU_API_KEY"),
    "modelscope": ("modelscope_token", "MODELSCOPE_TOKEN"),
    "siliconflow": ("siliconflow_api_key", "SILICONFLOW_API_KEY"),
    "seedance": ("seedance_api_key", "ARK_API_KEY"),
    "agnes": ("agnes_api_key", "AGNES_API_KEY"),
    "kling": ("kling_api_key", "KLING_API_KEY"),
    "dashscope": ("dashscope_api_key", "DASHSCOPE_API_KEY"),
    "pollinations": (None, "POLLINATIONS_KEY"),
}


def _read_settings_file() -> Dict[str, Any]:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# 由设置页灌进环境变量的键（值也留着，好在用户改 Key 时能覆盖自己、不覆盖 .env）
_INJECTED: Dict[str, str] = {}


def apply_settings_to_env(settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """把设置页里填的键同步到环境变量，返回本次生效的环境变量名。

    为什么非要有这一步：供应商类取 Key 一律读 ``os.getenv``，而设置页保存只写
    ``frontend/data/settings.json``（Next 侧的 /api/settings 不经过后端），
    不桥接就会出现「自检说已配置，真跑起来报缺 Key」。
    规则：.env / 系统环境优先，本函数只填空位和自己写过的值，
    所以手改 .env 的人不会被设置页悄悄盖掉。
    """
    data = settings if settings is not None else _read_settings_file()
    touched: List[str] = []
    for field, env in _KEY_TO_ENV.items():
        val = str(data.get(field) or "").strip()
        cur = os.getenv(env) or ""
        if not val:
            if _INJECTED.get(env) == cur:
                os.environ.pop(env, None)
                _INJECTED.pop(env, None)
            continue
        if cur and env not in _INJECTED:
            continue          # 外部（.env / 系统）已经定了，尊重它
        if cur != val:
            os.environ[env] = val
        _INJECTED[env] = val
        touched.append(env)
    return touched


def load_settings() -> Dict[str, Any]:
    """读设置；环境变量优先级更高（部署时用 env 覆盖）。"""
    data: Dict[str, Any] = _read_settings_file()
    apply_settings_to_env(data)
    for field, env in _KEY_TO_ENV.items():
        if os.getenv(env):
            data[field] = os.getenv(env)
    return data


def _has(name: str, settings: Dict[str, Any]) -> bool:
    if name == "comfyui":
        # 只认用户在设置页点过「Check Connection」的标记，避免仅因配置了
        # COMFYUI_SERVER_URL（默认值就有）就把它当可用通道挑出来。
        return bool(settings.get("comfyui_available"))
    if name == "http":
        return bool(os.getenv("IMAGE_API_URL"))
    field, env = _REQUIREMENT.get(name, (None, None))
    if field and str(settings.get(field) or "").strip():
        return True
    return bool(env and os.getenv(env))


def configured(modality: str, settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """返回该模态当前已配置的 provider 列表（按优先级排序）。"""
    s = settings if settings is not None else load_settings()
    order = IMAGE_PREFERENCE if modality == "image" else VIDEO_PREFERENCE
    return [p for p in order if _has(p, s)]


def pick(modality: str, preferred: Optional[str] = None,
         settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """挑一个能用的 provider。

    显式指定的 provider 只要已配置就优先用；没配置就按优先级顺延，
    并在返回里带上 `fallback_from` 说明为什么换，方便排查。
    """
    s = settings if settings is not None else load_settings()
    order = IMAGE_PREFERENCE if modality == "image" else VIDEO_PREFERENCE
    available = [p for p in order if _has(p, s)]

    if preferred:
        pref = str(preferred).lower()
        if pref in ("comfyui_image", "comfyui_video"):
            pref = "comfyui"
        if _has(pref, s) or pref not in _REQUIREMENT and pref in ("comfyui", "http"):
            return {"provider": pref, "source": "explicit", "available": available}
        if pref in _REQUIREMENT or pref == "http":
            # 显式指定但没配 Key：顺延到下一个可用的，并说明理由
            if available:
                return {"provider": available[0], "source": "fallback",
                        "fallback_from": pref, "available": available}
            return {"provider": pref, "source": "explicit_unconfigured", "available": []}
        return {"provider": pref, "source": "explicit", "available": available}

    if available:
        return {"provider": available[0], "source": "auto", "available": available}
    return {"provider": None, "source": "none_configured", "available": []}


def missing_hint(modality: str) -> str:
    return (
        f"没有任何可用的{modality}通道。请在「设置」页至少填一个 Key："
        "智谱（免费，推荐）/ 魔搭（免费额度）/ 硅基流动（只覆盖生图），然后点保存。"
    )
