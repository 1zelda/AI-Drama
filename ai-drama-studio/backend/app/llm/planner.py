"""LLM services for drama planning and character/episode generation."""
from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# Settings saved through the UI live in frontend/data/settings.json
_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "frontend" / "data" / "settings.json"

SYSTEM_PROMPT = """You are an expert AI drama director and scriptwriter. You help users plan AI-generated short drama series.
Your expertise includes: story structure, character development, visual direction, and episode planning.
Always respond in the same language as the user. Be creative, detailed, and practical."""


def _load_settings() -> Dict[str, Any]:
    """Merge UI settings over env defaults; UI settings win when present."""
    settings: Dict[str, Any] = {}
    if _SETTINGS_PATH.exists():
        try:
            settings = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            settings = {}
    api_key = settings.get("api_key") or os.getenv("OPENAI_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
    base_url = settings.get("base_url") or os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    model = settings.get("model") or os.getenv("LLM_MODEL", "deepseek-chat")
    return {"api_key": api_key, "base_url": base_url, "model": model}


def _get_client() -> AsyncOpenAI:
    s = _load_settings()
    return AsyncOpenAI(api_key=s["api_key"] or "missing-key", base_url=s["base_url"])


def llm_configured() -> bool:
    key = _load_settings()["api_key"]
    return bool(key) and not key.startswith("sk-your")


class LLMNotConfiguredError(RuntimeError):
    pass


def _ensure_configured() -> None:
    if not llm_configured():
        raise LLMNotConfiguredError(
            "LLM 未配置或 API Key 无效。请在 Settings 页面填入有效的 api_key（DeepSeek/OpenAI 兼容端点）。"
        )


async def plan_drama(title: str, description: str = "", existing_context: dict = None) -> dict:
    """Generate a complete drama plan: synopsis, characters, episodes."""
    _ensure_configured()
    model = _load_settings()["model"]
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

    client = _get_client()
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    return json.loads(response.choices[0].message.content)


async def chat_with_planner(project_context: dict, user_message: str) -> dict:
    """Interactive chat for refining drama details."""
    _ensure_configured()
    model = _load_settings()["model"]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if project_context:
        messages.append({"role": "user", "content": f"Current project state:\n{json.dumps(project_context, ensure_ascii=False)}"})
    messages.append({"role": "user", "content": user_message})

    client = _get_client()
    response = await client.chat.completions.create(
        model=model,
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
    _ensure_configured()
    model = _load_settings()["model"]
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
    client = _get_client()
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.5,
    )
    return json.loads(response.choices[0].message.content)
