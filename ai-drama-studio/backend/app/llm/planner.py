"""LLM services for drama planning and character/episode generation."""
from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
from openai import AsyncOpenAI
from dotenv import load_dotenv

# .env 落在项目根（ai-drama-studio/.env），但 uvicorn 常以 backend/ 为 CWD 启动，
# 裸 load_dotenv() 会找不到它，导致 LLM Key 为空。这里显式从两个候选位置加载。
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _BACKEND_ROOT.parent
for _candidate in (_PROJECT_ROOT / ".env", _BACKEND_ROOT / ".env"):
    if _candidate.exists():
        load_dotenv(_candidate, override=False)
        break
else:
    load_dotenv()


def _settings() -> Dict[str, Any]:
    """设置页存的 Key（frontend/data/settings.json）作为 env 的兜底。"""
    try:
        from ..api.settings import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


# 最近一次实际使用的通道（自检页展示用）。降级时能看到「走了哪条路」，
# 否则显示为 deepseek 实际却跑在智谱上，排查余额问题时会被误导。
LAST_CALL: Dict[str, str] = {"provider": "", "model": ""}

# 智谱 GLM-4-Flash：官方免费，OpenAI 兼容，国内直连。
# 一个智谱 Key 就能同时覆盖 LLM + 生图(cogview) + 生视频(cogvideox)，是最省事的起步组合。
ZHIPU_LLM = {
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "model": "glm-4-flash",
}


def _zhipu_config() -> Optional[Dict[str, str]]:
    cfg = _settings()
    key = os.getenv("ZHIPU_API_KEY") or cfg.get("zhipu_api_key") or ""
    if not key:
        return None
    model = (os.getenv("ZHIPU_LLM_MODEL") or ZHIPU_LLM["model"])
    return {"api_key": key, "base_url": ZHIPU_LLM["base_url"], "model": model, "provider": "zhipu"}


def llm_config() -> Dict[str, str]:
    """解析 LLM 连接参数：环境变量优先，其次设置页；缺失时回落智谱免费档。"""
    cfg = _settings()
    provider = (os.getenv("LLM_PROVIDER") or cfg.get("llm_provider") or "deepseek").lower()

    if provider == "zhipu":
        zhipu = _zhipu_config()
        if zhipu:
            return zhipu

    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
               or cfg.get("api_key") or "")
    base_url = (os.getenv("OPENAI_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL")
                or cfg.get("base_url") or "https://api.deepseek.com/v1")
    model = os.getenv("LLM_MODEL") or cfg.get("model") or "deepseek-chat"

    if not api_key:
        # 没填 deepseek/openai 的 Key，但填了智谱的 → 直接用智谱免费档，别让用户再注册一家
        zhipu = _zhipu_config()
        if zhipu:
            return {**zhipu, "provider": "zhipu(fallback)"}

    return {"api_key": api_key, "base_url": base_url, "model": model, "provider": provider}


def get_client() -> AsyncOpenAI:
    """每次调用都重新解析配置，改设置页不用重启后端。"""
    cfg = llm_config()
    if not cfg["api_key"]:
        raise RuntimeError(
            "未配置 LLM API Key：请在「设置」页填写并保存。"
            "最省事的是填一个智谱 Key（GLM-4-Flash 免费）。"
        )
    return AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])


def get_model() -> str:
    return llm_config()["model"]


async def chat_completion(**kwargs) -> str:
    """统一的对话入口；model 缺省时用设置页模型。

    主通道失败（余额不足 / Key 失效 / 限流）时自动降级到智谱免费档重试一次。
    实测 DeepSeek 余额耗尽是最高频的翻车原因，用户看到的不该是 402 而是成片。
    """
    cfg = llm_config()
    kwargs.setdefault("model", cfg["model"])
    try:
        response = await get_client().chat.completions.create(**kwargs)
        LAST_CALL["provider"] = cfg.get("provider", "")
        LAST_CALL["model"] = cfg["model"]
        return response.choices[0].message.content or ""
    except Exception as primary_err:  # noqa: BLE001
        fallback = _zhipu_config()
        if not fallback or fallback["base_url"] == cfg["base_url"]:
            raise
        retry = dict(kwargs)
        retry["model"] = fallback["model"]
        try:
            client = AsyncOpenAI(api_key=fallback["api_key"], base_url=fallback["base_url"])
            response = await client.chat.completions.create(**retry)
            LAST_CALL["provider"] = f"zhipu(主通道 {cfg.get('provider', '')} 失败后降级)"
            LAST_CALL["model"] = fallback["model"]
            return response.choices[0].message.content or ""
        except Exception:
            raise primary_err


# 视觉模型：给生图质检（QC 门）用。默认走智谱 glm-4v-flash（免费档），
# 也可以单独配 VISION_* / 设置页 vision_* 指向任意 OpenAI 兼容的 VLM。
VISION_DEFAULT = {
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "model": "glm-4v-flash",
}


def vision_config() -> Optional[Dict[str, str]]:
    """视觉模型连接参数；一个可用通道都没有时返回 None（QC 门自动跳过）。"""
    cfg = _settings()
    zhipu_key = os.getenv("ZHIPU_API_KEY") or cfg.get("zhipu_api_key") or ""
    key = (os.getenv("VISION_API_KEY") or cfg.get("vision_api_key") or "")

    if key:
        base_url = (os.getenv("VISION_BASE_URL") or cfg.get("vision_base_url")
                    or os.getenv("OPENAI_BASE_URL") or cfg.get("base_url")
                    or "https://api.openai.com/v1")
        return {"api_key": key, "base_url": base_url,
                "model": os.getenv("VISION_MODEL") or cfg.get("vision_model") or "gpt-4o-mini"}
    if zhipu_key:
        return {"api_key": zhipu_key, "base_url": VISION_DEFAULT["base_url"],
                "model": os.getenv("VISION_MODEL") or cfg.get("vision_model")
                or VISION_DEFAULT["model"]}

    base = llm_config()
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or cfg.get("api_key") or ""
    if api_key:
        return {"api_key": api_key, "base_url": base["base_url"],
                "model": os.getenv("VISION_MODEL") or cfg.get("vision_model") or base["model"]}
    return None


async def vision_completion(prompt: str, images, model: Optional[str] = None,
                            max_tokens: int = 800) -> str:
    """带图的对话：OpenAI 兼容的多模态 content，本地图转 data URL 传入。

    images 可以是本地路径也可以是 http(s) URL。没有可用视觉模型时抛异常，
    调用方（QC 门）会自己降级跳过。
    """
    import base64
    import mimetypes

    cfg = vision_config()
    if not cfg:
        raise RuntimeError("未配置可用的视觉模型（VISION_API_KEY / ZHIPU_API_KEY）")

    content: list = [{"type": "text", "text": prompt}]
    for image in list(images or [])[:4]:
        text = str(image)
        if text.startswith(("http://", "https://")):
            url = text
        else:
            path = Path(text)
            if not path.exists():
                continue
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            with open(path, "rb") as f:
                url = f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"
        content.append({"type": "image_url", "image_url": {"url": url}})

    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    response = await client.chat.completions.create(
        model=model or cfg["model"],
        messages=[{"role": "user", "content": content}],
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


# 兼容旧调用点：模块级客户端。配置缺失时保持惰性，避免在 import 阶段就炸。
_openai_client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")),
    base_url=os.getenv("OPENAI_BASE_URL", os.getenv("DEEPSEEK_BASE_URL", "https://api.openai.com/v1")),
)

SYSTEM_PROMPT = """You are an expert AI drama director and scriptwriter. You help users plan AI-generated short drama series.
Your expertise includes: story structure, character development, visual direction, and episode planning.
Always respond in the same language as the user. Be creative, detailed, and practical."""


async def plan_drama(title: str, description: str = "", existing_context: dict = None) -> dict:
    """Generate a complete drama plan: synopsis, characters, episodes."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if existing_context:
        messages.append({"role": "user", "content": f"Current context: {json.dumps(existing_context, ensure_ascii=False)}"})
    messages.append({
        "role": "user",
        "content": (
            f"Create a complete drama plan for: '{title}'\n"
            f"Description: {description or 'No additional description'}\n\n"
            f"Output JSON with these sections:\n"
            f"{{\n"
            f'  "synopsis": "one-paragraph story summary",\n'
            f'  "genre": "drama/comedy/horror/etc",\n'
            f'  "tone": "dark/light/romantic/etc",\n'
            f'  "total_episodes": 12,\n'
            f'  "episode_duration_seconds": 60,\n'
            f'  "characters": [\n'
            f'    {{"name": "...", "role": "protagonist/antagonist/supporting", "age": 25, "personality": "...", "appearance": "...", "costume_style": "..."}}\n'
            f'  ],\n'
            f'  "episodes": [\n'
            f'    {{"number": 1, "title": "...", "summary": "...", "key_scenes": ["scene1", "scene2"]}}\n'
            f'  ]\n'
            f"}}\n"
            f"Be detailed and creative. The drama should be engaging and visually interesting for AI generation."
        ),
    })

    response = await get_client().chat.completions.create(
        model=get_model(),
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    return json.loads(response.choices[0].message.content)


async def chat_with_planner(project_context: dict, user_message: str) -> dict:
    """Interactive chat for refining drama details."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if project_context:
        messages.append({"role": "user", "content": f"Current project state:\n{json.dumps(project_context, ensure_ascii=False)}"})
    messages.append({"role": "user", "content": user_message})

    response = await get_client().chat.completions.create(
        model=get_model(),
        messages=messages,
        temperature=0.8,
        max_tokens=2000,
    )
    reply = response.choices[0].message.content

    # Parse suggestions if present
    suggestions = []
    if "suggestions:" in reply:
        parts = reply.split("suggestions:")
        reply = parts[0].strip()
        sug_text = parts[1].strip()
        suggestions = [s.strip() for s in sug_text.split("\n") if s.strip()]

    return {"reply": reply, "suggestions": suggestions}


async def generate_storyboard(episode_data: dict, characters: list) -> dict:
    """Generate detailed storyboard for an episode."""
    messages = [
        {"role": "system", "content": "You are a professional storyboard artist for AI video generation."},
        {"role": "user", "content": f"""Generate detailed storyboard shots for this episode:
Episode: {episode_data.get('title', 'Untitled')}
Summary: {episode_data.get('summary', '')}
Characters: {json.dumps(characters, ensure_ascii=False)}

Output JSON:
{{
  "shots": [
    {{
      "shot_number": 1,
      "type": "establishing/medium/close-up",
      "camera": "fixed/push-in/pan/track",
      "description": "detailed visual description for AI image generation",
      "dialogue": "character dialogue if any",
      "duration_seconds": 5,
      "mood": "emotion/tone",
      "lighting": "natural/dramatic/sunset/etc",
      "composition": "close-up/medium-wide/landscape"
    }}
  ]
}}"""},
    ]
    response = await get_client().chat.completions.create(
        model=get_model(),
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.5,
    )
    return json.loads(response.choices[0].message.content)