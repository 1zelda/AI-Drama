"""音频供应商层：克隆 TTS（GPT-SoVITS / edge-tts）+ AI 音乐（ACE-Step）+ LLM 写歌词。

部署形态：本机只做编排，模型跑在别处——
  · edge-tts      纯本地免费（无克隆能力，兜底用）
  · GPT-SoVITS    Windows 一键包 / 局域网机，api_v2 监听 9880；零样本克隆只需参考音频+其文字稿
  · ACE-Step      需 GPU（远程租卡/另一台机），infer-api.py 监听 8001
  · 歌声换声(RVC) 在 RVC WebUI 训练好音色模型后人工/脚本后处理（见部署指南）
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

VOICE_DIR = Path(__file__).resolve().parents[2] / "data" / "voices"
AUDIO_OUT = Path(__file__).resolve().parents[2] / "output" / "audio"


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #

async def tts_edge(text: str, voice: str = "zh-CN-YunxiNeural",
                   rate: str = "+0%", pitch: str = "+0Hz") -> bytes:
    """微软 edge-tts：免费、无需部署，但音色固定不可克隆。"""
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    chunks: List[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


async def tts_gptsovits(text: str, ref_audio_path: str, prompt_text: str = "",
                        prompt_lang: str = "zh", text_lang: str = "zh",
                        base_url: Optional[str] = None,
                        aux_ref_audio_paths: Optional[List[str]] = None) -> bytes:
    """GPT-SoVITS api_v2 零样本克隆：参考音频 + 其文字稿 → 克隆音色朗读。

    ref_audio_path 是 GPT-SoVITS 所在机器上的路径（本机部署即本机路径）。
    """
    url = (base_url or os.getenv("GPTSOVITS_URL", "http://127.0.0.1:9880")).rstrip("/")
    payload: Dict[str, Any] = {
        "text": text,
        "text_lang": text_lang,
        "ref_audio_path": ref_audio_path,
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang,
        "media_type": "wav",
        "streaming_mode": False,
    }
    if aux_ref_audio_paths:
        payload["aux_ref_audio_paths"] = aux_ref_audio_paths
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{url}/tts", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"GPT-SoVITS 失败（HTTP {resp.status_code}）: {resp.text[:300]}")
        return resp.content


# --------------------------------------------------------------------------- #
# AI 音乐（ACE-Step）
# --------------------------------------------------------------------------- #

async def music_acestep(prompt: str, lyrics: str, duration: float = 60.0,
                        base_url: Optional[str] = None,
                        infer_steps: int = 60) -> Dict[str, Any]:
    """ACE-Step /generate：风格标签 + [verse]/[chorus] 结构歌词 → 完整歌曲。

    返回原始响应（含音频路径/URL 字段，容错解析）。生成的歌声是模型默认音色，
    要换成克隆音色需再走 RVC 声音转换（见部署指南）。
    """
    url = (base_url or os.getenv("ACESTEP_URL", "http://127.0.0.1:8001")).rstrip("/")
    payload = {
        "prompt": prompt,               # 风格标签，如 "cinematic, epic rock, male vocal"
        "lyrics": lyrics,
        "audio_duration": duration,
        "infer_steps": infer_steps,
    }
    async with httpx.AsyncClient(timeout=1800.0) as client:
        resp = await client.post(f"{url}/generate", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"ACE-Step 失败（HTTP {resp.status_code}）: {resp.text[:300]}")
        data = resp.json()

    # 容错提取音频路径/URL
    audio_refs: List[str] = []

    def walk(node: Any):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("audio", "audios", "path", "paths", "url", "urls") :
                    if isinstance(v, str):
                        audio_refs.append(v)
                    elif isinstance(v, list):
                        audio_refs.extend(str(x) for x in v if isinstance(x, str))
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return {"raw": data, "audio_refs": audio_refs}


async def health(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(base_url.rstrip("/") + "/health")
            return resp.status_code == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# LLM 写歌词
# --------------------------------------------------------------------------- #

async def write_lyrics(theme: str, style_tags: str, duration: float,
                       language: str = "中文") -> str:
    """按 ACE-Step 歌词格式（[verse]/[chorus] 结构标签）生成歌词。"""
    from ..llm.planner import chat_completion

    prompt = f"""你是顶级作词人。为一首 {duration:.0f} 秒的 {style_tags} 风格歌曲写歌词。
主题：{theme}
语言：{language}

ACE-Step 歌词格式要求：用结构标签分段，如
[verse]
歌词行…
[chorus]
歌词行…
[bridge]
歌词行…

按 {duration:.0f} 秒控制篇幅（约每秒 2-3 字）。只输出带结构标签的歌词本身，不要解释。"""
    return await chat_completion(
        messages=[{"role": "user", "content": prompt}], temperature=0.9
    ) or ""


# --------------------------------------------------------------------------- #
# 音色库（data/voices/*.json）
# --------------------------------------------------------------------------- #

@dataclass
class VoiceProfile:
    name: str
    engine: str                 # gptsovits
    ref_audio: str              # 参考音频路径（GPT-SoVITS 机器上的路径）
    prompt_text: str            # 参考音频的文字稿
    prompt_lang: str = "zh"
    note: str = ""

    def to_json(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def list_voices() -> List[Dict[str, Any]]:
    if not VOICE_DIR.exists():
        return []
    out = []
    for f in sorted(VOICE_DIR.glob("*.json")):
        try:
            v = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ref = v.get("ref_audio") or ""
        if ref and not Path(ref).exists():
            # 换机/搬家后绝对路径失效：参考音频其实都收在库目录里，按名字找回来
            name = v.get("name") or f.stem
            cand = next((c for c in sorted((VOICE_DIR / "ref_audios").glob(f"{name}.*"))), None) if (VOICE_DIR / "ref_audios").exists() else None
            if cand:
                v["ref_audio"] = str(cand.resolve())
                f.write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")
        out.append(v)
    return out


def save_voice(profile: VoiceProfile) -> Dict[str, Any]:
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    (VOICE_DIR / f"{profile.name}.json").write_text(
        json.dumps(profile.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return profile.to_json()


def save_ref_audio(name: str, content: bytes, ext: str = "wav") -> str:
    """把上传的参考音频存进音色库目录，返回绝对路径（填给 GPT-SoVITS）。"""
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    (VOICE_DIR / "ref_audios").mkdir(exist_ok=True)
    target = VOICE_DIR / "ref_audios" / f"{name}.{ext}"
    target.write_bytes(content)
    return str(target.resolve())
