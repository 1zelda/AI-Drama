"""配音与 AI 音乐 API。

声音克隆路线：上传元首（或其他角色）参考音频 + 文字稿 → GPT-SoVITS api_v2 零样本克隆朗读。
AI 音乐路线：主题 → LLM 写歌词（[verse]/[chorus] 结构）→ ACE-Step /generate 出曲 →
（可选）RVC 声音转换把歌声换成克隆音色（见 drama-pipeline/音频克隆与AI音乐部署.md）。
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..providers import audio_providers as ap

router = APIRouter(prefix="/api/audio", tags=["audio"])

AUDIO_OUT = Path(__file__).resolve().parents[2] / "output" / "audio"
AUDIO_OUT.mkdir(parents=True, exist_ok=True)
router.mount("/files", StaticFiles(directory=str(AUDIO_OUT)), name="audio_files")


# --------------------------- 音色库 --------------------------- #

class VoiceAddRequest(BaseModel):
    name: str
    ref_audio_b64: str = Field(..., description="参考音频 base64（wav/mp3，建议 5-15 秒清晰人声）")
    ref_audio_ext: str = "wav"
    prompt_text: str = Field(..., description="参考音频的文字稿（逐字对应，克隆质量的关键）")
    prompt_lang: str = "zh"
    note: str = ""


@router.get("/voices")
async def voices():
    return {"voices": ap.list_voices()}


@router.post("/voices")
async def add_voice(req: VoiceAddRequest):
    try:
        content = base64.b64decode(req.ref_audio_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="参考音频 base64 解码失败")
    if len(content) < 2000:
        raise HTTPException(status_code=400, detail="参考音频太小，请上传 5-15 秒清晰人声")
    path = ap.save_ref_audio(req.name, content, req.ref_audio_ext.lstrip("."))
    profile = ap.VoiceProfile(
        name=req.name, engine="gptsovits", ref_audio=path,
        prompt_text=req.prompt_text, prompt_lang=req.prompt_lang, note=req.note,
    )
    return {"voice": ap.save_voice(profile)}


# --------------------------- TTS --------------------------- #

class TTSRequest(BaseModel):
    text: str
    engine: str = Field(default="edge", description="edge | gptsovits")
    voice_name: Optional[str] = Field(default=None, description="gptsovits：音色库中的名字")
    voice: str = Field(default="zh-CN-YunxiNeural", description="edge：音色名")
    rate: str = "+0%"
    filename: str = ""


@router.post("/tts")
async def tts(req: TTSRequest):
    ts = int(time.time() * 1000) % 10**10
    fname = req.filename or f"tts_{ts}"
    try:
        if req.engine == "gptsovits":
            matched = [v for v in ap.list_voices() if v["name"] == req.voice_name]
            if not matched:
                raise HTTPException(status_code=404, detail=f"音色 {req.voice_name!r} 不在音色库，先在「音色管理」添加")
            v = matched[0]
            content = await ap.tts_gptsovits(
                req.text, ref_audio_path=v["ref_audio"], prompt_text=v["prompt_text"],
                prompt_lang=v.get("prompt_lang", "zh"),
            )
            out = AUDIO_OUT / f"{fname}.wav"
        else:
            content = await ap.tts_edge(req.text, voice=req.voice, rate=req.rate)
            out = AUDIO_OUT / f"{fname}.mp3"
        out.write_bytes(content)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    return {"url": f"/api/audio/files/{out.name}", "local_path": str(out)}


# --------------------------- AI 音乐 --------------------------- #

class LyricsRequest(BaseModel):
    theme: str
    style_tags: str = "epic rock, dramatic, male vocal"
    duration: float = Field(default=60, ge=15, le=240)
    language: str = "中文"


@router.post("/lyrics")
async def lyrics(req: LyricsRequest):
    try:
        text = await ap.write_lyrics(req.theme, req.style_tags, req.duration, req.language)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"写歌词失败：{exc}")
    return {"lyrics": text}


class SongRequest(BaseModel):
    theme: str = ""
    style_tags: str = "epic rock, dramatic, male vocal"
    lyrics: str = Field(..., description="歌词（可用 /lyrics 生成后手改）")
    duration: float = Field(default=60, ge=15, le=240)


@router.post("/song")
async def song(req: SongRequest):
    try:
        result = await ap.music_acestep(req.style_tags, req.lyrics, req.duration)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    if not result["audio_refs"]:
        raise HTTPException(status_code=500, detail=f"ACE-Step 未返回音频：{req.__class__} {str(result['raw'])[:300]}")
    return result


@router.get("/services")
async def services():
    """探测 GPT-SoVITS / ACE-Step 是否在线。"""
    import os
    gptsovits_url = os.getenv("GPTSOVITS_URL", "http://127.0.0.1:9880")
    acestep_url = os.getenv("ACESTEP_URL", "http://127.0.0.1:8001")
    return {
        "gptsovits_url": gptsovits_url,
        "gptsovits_online": await ap.health(gptsovits_url),
        "acestep_url": acestep_url,
        "acestep_online": await ap.health(acestep_url),
        "hint": "两服务默认不随本平台启动；部署步骤见 drama-pipeline/音频克隆与AI音乐部署.md",
    }
