"""剧情工坊 API — 素材+题材 → 自动生成剧情 → 可改可覆盖重生成 → 对话讨论。

范式：
1. POST /api/story/generate  素材+题材+集数设定 → 剧情内容（Markdown，含梗概/人物/分集大纲）
   current_content 非空时视为「重新生成」，LLM 会参考旧稿与用户的修改意见。
2. POST /api/story/chat      带完整对话记忆的讨论（session 内多轮，AI 看得到素材与当前剧情稿）
3. GET  /api/story/session/{sid}   读回会话（内容+输入+聊天记录），刷新不丢
4. PUT  /api/story/session/{sid}   保存用户手改后的内容

会话按 session_id 持久化在 backend/data/story_workshop/ 下（JSON 文件）。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/story", tags=["story"])

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "story_workshop"
MAX_HISTORY = 20

GENRES = [
    "搞笑鬼畜", "逆袭爽剧", "悬疑反转", "甜宠恋爱", "科幻末日",
    "古装仙侠", "都市现实", "恐怖惊悚", "同人二创", "温情治愈",
]


def _gen_prompt(material: str, genre: str, mode: str, episode_count: int,
                instruction: str, current_content: str) -> str:
    ep_spec = (
        f"单集短剧（只写 1 集，做深做透）"
        if mode == "single"
        else f"连续剧，共 {episode_count} 集，集与集之间要有钩子衔接"
    )
    regen = ""
    if current_content:
        regen = (
            "\n\n【这是重新生成】用户对上一版不满意。上一版内容如下（供参考，避免重复同样的问题）：\n"
            f"<上一版>\n{current_content[:4000]}\n</上一版>\n"
            + (f"用户的修改意见：{instruction}\n" if instruction else "用户没有具体说哪里不满意，请换一套明显不同的思路重写。\n")
        )
    elif instruction:
        regen = f"\n\n用户的补充要求：{instruction}\n"

    return f"""你是顶级短剧编剧。请根据用户提供的素材与题材设定，创作一部 AI 漫剧/短剧的完整剧情。

【题材】{genre}
【体量】{ep_spec}
【素材】{material or '（无素材，请围绕题材原创）'}
{regen}
输出要求（Markdown，中文）：
# 《剧名》
## 一句话梗概
（一句话，有冲突有钩子）
## 核心人物
（每位一段：名字 / 身份 / 性格 / 外形特征（供 AI 生图用，要具体到发色服装）/ 口头禅或记忆点）
## 分集剧情
（每集一节：### 第 N 集《标题》
- 剧情梗概：150-250 字，写出起承转合
- 爆点/钩子：本集最抓人的画面或反转
- 结尾悬念：下一集钩子（单集则写结尾余韵））
## 改编亮点
（2-3 条：为什么这样改编效果好）

只输出 Markdown 本身，不要解释。"""


class GenerateRequest(BaseModel):
    session_id: Optional[str] = None
    material: str = ""
    genre: str = "搞笑鬼畜"
    mode: str = Field(default="single", description="single | series")
    episode_count: int = Field(default=1, ge=1, le=24)
    instruction: str = ""
    current_content: str = ""


class ChatRequest(BaseModel):
    session_id: str
    message: str


class SessionSaveRequest(BaseModel):
    content: str


def _load_session(sid: str) -> Dict[str, Any]:
    path = DATA_DIR / f"{sid}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"session_id": sid, "content": "", "inputs": {}, "history": [], "updated_at": 0}


def _save_session(data: Dict[str, Any]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.time()
    (DATA_DIR / f"{data['session_id']}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.get("/genres")
async def list_genres():
    return {"genres": GENRES}


@router.post("/generate")
async def generate(req: GenerateRequest):
    sid = req.session_id or uuid.uuid4().hex[:12]
    session = _load_session(sid)
    prompt = _gen_prompt(req.material, req.genre, req.mode, req.episode_count,
                         req.instruction, req.current_content or session.get("content", ""))
    try:
        # 走统一入口：主通道（DeepSeek）余额不足/失效时自动降级智谱免费档
        from ..llm.planner import chat_completion
        content = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.85,
        ) or ""
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"剧情生成失败：{exc}")

    session["content"] = content
    session["inputs"] = {
        "material": req.material, "genre": req.genre, "mode": req.mode,
        "episode_count": req.episode_count,
    }
    session.setdefault("history", []).append(
        {"role": "system", "content": f"[{'重新生成' if req.current_content else '生成'}剧情] 题材={req.genre} 体量={'单集' if req.mode == 'single' else f'{req.episode_count}集'}"}
    )
    session["history"] = session["history"][-MAX_HISTORY:]
    _save_session(session)
    return {"session_id": sid, "content": content}


@router.post("/chat")
async def chat(req: ChatRequest):
    session = _load_session(req.session_id)

    inputs = session.get("inputs", {})
    context_note = (
        f"当前工坊设定——题材：{inputs.get('genre', '未定')}；"
        f"体量：{'单集' if inputs.get('mode') == 'single' else str(inputs.get('episode_count', '?')) + '集'}；"
        f"素材：{inputs.get('material', '')[:1500] or '无'}\n"
        f"当前剧情稿（用户可能已手动修改）：\n{session.get('content', '')[:4000] or '（尚未生成）'}"
    )
    system = (
        "你是短剧编剧团队的金牌策划，正在和用户讨论并打磨一部 AI 漫剧的剧情。"
        "用户会提想法、让你改稿方向、问你的判断——给出具体、可执行、有创造力的回答；"
        "讨论结论若影响剧情稿，直接给出可替换的文本文段。中文回答，简洁有梗。\n\n" + context_note
    )
    history: List[Dict[str, str]] = [
        {"role": h["role"], "content": h["content"]}
        for h in session.get("history", [])
        if h.get("role") in ("user", "assistant")
    ][-MAX_HISTORY:]
    history.append({"role": "user", "content": req.message})

    try:
        from ..llm.planner import chat_completion
        reply = await chat_completion(
            messages=[{"role": "system", "content": system}] + history,
            temperature=0.8,
        ) or ""
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"对话失败：{exc}")

    session.setdefault("history", []).extend([
        {"role": "user", "content": req.message},
        {"role": "assistant", "content": reply},
    ])
    session["history"] = session["history"][-MAX_HISTORY * 2:]
    _save_session(session)
    return {"reply": reply, "session_id": req.session_id}


@router.get("/session/{sid}")
async def get_session(sid: str):
    return _load_session(sid)


@router.put("/session/{sid}")
async def save_session(sid: str, req: SessionSaveRequest):
    session = _load_session(sid)
    session["content"] = req.content
    _save_session(session)
    return {"ok": True}
