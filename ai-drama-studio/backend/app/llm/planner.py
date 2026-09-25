"""LLM services for drama planning and character/episode generation."""
from __future__ import annotations
import os
import json
from typing import Optional, Dict, Any
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# Support both OpenAI and DeepSeek-compatible APIs
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

    response = await _openai_client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "gpt-4o"),
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

    response = await _openai_client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "gpt-4o"),
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
    response = await _openai_client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "gpt-4o"),
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.5,
    )
    return json.loads(response.choices[0].message.content)