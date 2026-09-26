"""Settings API routes."""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json
import os
from pathlib import Path

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_PATH = Path(__file__).parent.parent.parent.parent / "frontend" / "data" / "settings.json"
ENV_PATH = Path(__file__).parent.parent.parent.parent / ".env"

DEFAULT_SETTINGS = {
    "llm_provider": "deepseek",
    "api_key": "",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "comfyui_url": "http://localhost:8188",
    "comfyui_available": False,
    "agnes_api_key": "",
    "agnes_proxy_url": "http://127.0.0.1:57324",
    "agnes_video_model": "agnes-video-2.5-flash",
    "agnes_video_mode": "T2V",
    "modelscope_token": "",
    "siliconflow_api_key": "",
    "gemini_api_key": "",
    "zhipu_api_key": "",
    "kling_api_key": "",
    "kling_api_url": "https://api.klingai.com/v1",
    "seedance_api_key": "",
    "veo_api_key": "",
    "pollinations_key": "",
    "dashscope_api_key": "",
    "vision_api_key": "",
    "vision_base_url": "",
    "vision_model": "",
    "public_asset_base_url": "",
    "gptsovits_url": "",
    "auto_cleanup": False,
}


# (环境变量, 设置页字段, 字段为空时的默认值)。GET 回填和 PUT 落盘共用这份映射。
ENV_MAPPING = [
    ("OPENAI_API_KEY", "api_key", None),
    ("OPENAI_BASE_URL", "base_url", "https://api.deepseek.com/v1"),
    ("LLM_MODEL", "model", "deepseek-chat"),
    ("COMFYUI_SERVER_URL", "comfyui_url", "http://localhost:8188"),
    ("AGNES_API_KEY", "agnes_api_key", None),
    ("AGNES_PROXY_URL", "agnes_proxy_url", "http://127.0.0.1:57324"),
    ("AGNES_VIDEO_MODEL", "agnes_video_model", "agnes-video-2.5-flash"),
    ("AGNES_VIDEO_MODE", "agnes_video_mode", "T2V"),
    ("MODELSCOPE_TOKEN", "modelscope_token", None),
    ("SILICONFLOW_API_KEY", "siliconflow_api_key", None),
    ("GEMINI_API_KEY", "gemini_api_key", None),
    ("ZHIPU_API_KEY", "zhipu_api_key", None),
    ("ARK_API_KEY", "seedance_api_key", None),
    ("KLING_API_KEY", "kling_api_key", None),
    ("KLING_API_URL", "kling_api_url", "https://api.klingai.com/v1"),
    ("POLLINATIONS_KEY", "pollinations_key", None),
    ("DASHSCOPE_API_KEY", "dashscope_api_key", None),
    ("VISION_API_KEY", "vision_api_key", None),
    ("VISION_BASE_URL", "vision_base_url", None),
    ("VISION_MODEL", "vision_model", None),
    ("PUBLIC_ASSET_BASE_URL", "public_asset_base_url", None),
    ("GPTSOVITS_URL", "gptsovits_url", None),
]


def _read_env() -> Dict[str, str]:
    """解析 .env。settings.json 丢失或被写成空键时，.env 是唯一的兜底数据源。"""
    if not ENV_PATH.exists():
        return {}
    try:
        text = ENV_PATH.read_text(encoding="utf-8-sig")
    except Exception:  # noqa: BLE001
        return {}
    values: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def _load_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            # 缺的字段补默认值，老文件不会因为少了一个新键就让前端拿到 undefined
            settings.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    # settings.json 里为空的键回退到 .env 现值。以前 json 一旦缺失/为空，
    # GET 返回空 Key → 前端原样 PUT → .env 里的真 Key 被保存动作抹掉。
    env = _read_env()
    for env_key, field, _default in ENV_MAPPING:
        if not str(settings.get(field) or "").strip():
            val = env.get(env_key)
            if val:
                settings[field] = val
    return settings


def _save_settings(data: Dict[str, Any]):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 同步到 backend 同级的 .env。这里是**合并**而不是整份重写：
    # 手动写在 .env 里的其它键（VISION_*、GPTSOVITS_URL、FFMPEG_BIN…）以前会被
    # 一次保存全部抹掉，用户在设置页点一下保存就把魔改线的配置弄没了。
    env_path = ENV_PATH
    # 这是**合并**而不是整份重写：手动写在 .env 里的其它键（FFMPEG_BIN…）不会被
    # 一次保存抹掉。这次请求里没出现的字段也**不碰**。
    updates: Dict[str, str] = {}
    for env_key, field, default in ENV_MAPPING:
        if field not in data:
            continue
        updates[env_key] = str(data.get(field) or "").strip() or (default or "")
    lines: list[str] = []
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8-sig").splitlines()
        except Exception:  # noqa: BLE001 - 读不动就当没有，按新写一份
            lines = []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if line.startswith("#") or "=" not in line or key not in updates:
            out.append(line)          # 注释和这份映射管不到的行原样留着
            continue
        seen.add(key)
        out.append(f"{key}={updates[key]}")
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    # 已经在跑的后端也要立刻用上新 Key：既然这次保存明确写了这些键，就以保存的值为准，
    # 免得出现「界面上是新 Key，进程里还在用旧 Key」。
    for env_key, val in updates.items():
        if val:
            os.environ[env_key] = val
        else:
            os.environ.pop(env_key, None)


@router.get("/")
async def get_settings():
    return _load_settings()


@router.put("/")
async def update_settings(req: Dict[str, Any]):
    settings = _load_settings()
    settings.update(req)
    _save_settings(settings)
    return settings
