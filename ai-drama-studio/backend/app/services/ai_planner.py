import os
import json
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")),
    base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
)

SYSTEM_PROMPT = "You are an AI drama director assistant. Help users plan their AI-generated drama series by creating story outlines, character profiles, and scene breakdowns. Always respond in the same language as the user."

async def plan_drama(title: str, existing_context: dict = None) -> dict:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if existing_context:
        messages.append({"role": "user", "content": f"Current project context: {json.dumps(existing_context)}"})
    messages.append({"role": "user", "content": f"Create a drama plan for: {title}"})
    response = await client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "gpt-4o"),
        messages=messages,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)
