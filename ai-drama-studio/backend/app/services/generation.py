"""Asset generation service: character sheets, storyboard images, episode videos.

Real generation goes through ComfyUI (images) and Agnes (video). When those
services are unreachable, we fall back to deterministic placeholder assets so
the full pipeline stays demoable and testable end-to-end.
"""
from __future__ import annotations
import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..llm.planner import generate_storyboard as llm_generate_storyboard

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"
ASSETS_DIR = DATA_DIR / "assets"
OUTPUT_DIR = DATA_DIR / "output"
CONFIG_DIR = BACKEND_DIR / "config"


def _project_asset_dir(project_id: str, kind: str) -> Path:
    d = ASSETS_DIR / project_id / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def _asset_url(project_id: str, kind: str, filename: str) -> str:
    return f"/assets/{project_id}/{kind}/{filename}"


# ---------------------------------------------------------------- ffmpeg util

def _ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return ""


async def _run(cmd: List[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    return proc.returncode or 0


async def render_placeholder_image(out_path: Path, label: str) -> bool:
    """Render a labelled placeholder PNG with Pillow (the bundled ffmpeg lacks drawtext)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (640, 360), (26, 26, 46))
    draw = ImageDraw.Draw(img)
    # vertical gradient-ish accent band
    for y in range(0, 360, 6):
        shade = 40 + int((y / 360) * 80)
        draw.line([(0, y), (640, y)], fill=(shade // 2, shade // 3, shade))
    font = _load_font(24)
    text = label if label else "placeholder"
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = len(text) * 12, 24
    draw.text(((640 - tw) / 2, (360 - th) / 2), text, fill=(230, 230, 240), font=font)
    try:
        img.save(out_path, "PNG")
        return out_path.exists()
    except Exception:
        return False


_font_cache: Dict[int, Any] = {}


def _load_font(size: int):
    from PIL import ImageFont
    import glob
    if size in _font_cache:
        return _font_cache[size]
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    # any CJK-capable noto font found on the system
    candidates += sorted(glob.glob("/usr/share/fonts/**/NotoSansCJK*", recursive=True))
    candidates += sorted(glob.glob("/usr/share/fonts/**/Noto*CJK*", recursive=True))
    font = None
    for c in candidates:
        try:
            font = ImageFont.truetype(c, size)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    _font_cache[size] = font
    return font


async def render_placeholder_clip(out_path: Path, duration: int) -> bool:
    """Render a placeholder video clip via ffmpeg lavfi."""
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return False
    duration = max(1, min(int(duration or 3), 10))
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=24:duration={duration}",
        "-pix_fmt", "yuv420p", "-t", str(duration),
        str(out_path),
    ]
    return await _run(cmd) == 0


# ---------------------------------------------------------------- ComfyUI

def _comfyui_client():
    from ..providers.comfyui import ComfyUIClient
    client = ComfyUIClient()
    return client if client.is_available() else None


def _load_workflow(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / "comfyui_workflows" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _patch_prompt_node(workflow: Dict[str, Any], class_types: List[str], text: str) -> Dict[str, Any]:
    """Best-effort: inject prompt text into the first positive prompt node."""
    w = json.loads(json.dumps(workflow))
    for nid, node in w.items():
        if node.get("class_type") in class_types and "text" in node.get("inputs", {}):
            node["inputs"]["text"] = text
            break
    return w


# ---------------------------------------------------------------- public API

async def generate_character_image(project_id: str, character: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a character reference sheet. ComfyUI if available, else placeholder."""
    char_id = character.get("id") or character.get("name") or uuid.uuid4().hex[:6]
    char_id = re.sub(r"[^\w\u4e00-\u9fff-]", "_", str(char_id))[:40]
    desc = character.get("appearance") or character.get("description") or character.get("name", "character")
    prompt = f"full body character reference sheet, {character.get('name', '')}, {desc}, {character.get('costume', '')}, white background, detailed face"
    out_dir = _project_asset_dir(project_id, "characters")
    filename = f"{char_id}.png"
    out_path = out_dir / filename

    client = _comfyui_client()
    if client:
        try:
            wf = _patch_prompt_node(_load_workflow("character-sheet"), ["CLIPTextEncode"], prompt)
            paths = client.generate(wf, "7", out_path, timeout=300)
            return {"character_id": char_id, "image": _asset_url(project_id, "characters", Path(paths[0]).name), "engine": "comfyui"}
        except Exception as e:
            return {"character_id": char_id, "image": None, "engine": "comfyui", "error": str(e)}

    ok = await render_placeholder_image(out_path, f"char {character.get('name', char_id)}")
    if not ok:
        return {"character_id": char_id, "image": None, "engine": "placeholder", "error": "ffmpeg unavailable"}
    return {"character_id": char_id, "image": _asset_url(project_id, "characters", filename), "engine": "placeholder"}


async def generate_storyboard_images(project_id: str, episode: Dict[str, Any]) -> Dict[str, Any]:
    """Generate one image per storyboard shot of an episode."""
    shots = episode.get("shots") or []
    if not shots:
        # derive shots from key_scenes as fallback
        shots = [{"description": s, "shot_number": i + 1} for i, s in enumerate(episode.get("key_scenes", []))]
    out_dir = _project_asset_dir(project_id, "storyboard")
    ep_num = episode.get("number", 0)
    results: List[Dict[str, Any]] = []
    client = _comfyui_client()

    for shot in shots:
        idx = shot.get("shot_number", len(results) + 1)
        filename = f"ep{ep_num}_shot{idx:03d}.png"
        out_path = out_dir / filename
        prompt = shot.get("description") or f"{episode.get('title', '')} scene"
        image_url = None
        engine = "placeholder"
        if client:
            try:
                wf = _patch_prompt_node(_load_workflow("scene-generation"), ["CLIPTextEncode"], prompt)
                paths = client.generate(wf, "7", out_path, timeout=300)
                image_url = _asset_url(project_id, "storyboard", Path(paths[0]).name)
                engine = "comfyui"
            except Exception:
                pass
        if image_url is None:
            if await render_placeholder_image(out_path, f"ep{ep_num} shot{idx}"):
                image_url = _asset_url(project_id, "storyboard", filename)
            else:
                engine = "failed"
        results.append({"shot_number": idx, "image": image_url, "engine": engine})

    return {"episode_number": ep_num, "images": results}


async def generate_episode_video(project_id: str, episode: Dict[str, Any], storyboard: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Generate an episode video: Agnes if reachable, else stitch storyboard images with ffmpeg."""
    ep_num = episode.get("number", 0)
    out_dir = OUTPUT_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"episode_{ep_num}.mp4"
    out_path = out_dir / filename

    # Try Agnes proxy first
    try:
        from ..providers.agnes import get_agnes_client
        client = get_agnes_client()
        if await client.health():
            prompt = episode.get("summary") or episode.get("title", "")
            result = await client.generate(prompt=prompt, output_dir=str(out_dir))
            if result.get("video_url"):
                return {"episode_number": ep_num, "video": result["video_url"], "engine": "agnes"}
    except Exception:
        pass

    # Fallback: stitch placeholder clips (one per shot)
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return {"episode_number": ep_num, "video": None, "engine": "failed", "error": "ffmpeg unavailable and Agnes unreachable"}

    shots = (storyboard or {}).get("shots") or episode.get("shots") or []
    if not shots:
        shots = [{"duration_seconds": 3} for _ in episode.get("key_scenes", []) or [1, 2, 3]]
    clips: List[Path] = []
    for i, shot in enumerate(shots):
        clip = out_dir / f"tmp_e{ep_num}_{i:03d}.mp4"
        if await render_placeholder_clip(clip, int(shot.get("duration_seconds", 3) or 3)):
            clips.append(clip)
    if not clips:
        return {"episode_number": ep_num, "video": None, "engine": "failed", "error": "no clips rendered"}

    list_file = out_dir / f"tmp_e{ep_num}_list.txt"
    list_file.write_text("\n".join(f"file '{p.name}'" for p in clips), encoding="utf-8")
    code = await _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                       "-c", "copy", str(out_path)])
    if code != 0:
        code = await _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                           "-pix_fmt", "yuv420p", str(out_path)])
    for p in clips:
        p.unlink(missing_ok=True)
    list_file.unlink(missing_ok=True)
    if code != 0 or not out_path.exists():
        return {"episode_number": ep_num, "video": None, "engine": "failed", "error": "ffmpeg concat failed"}
    return {"episode_number": ep_num, "video": f"/output/{project_id}/{filename}", "engine": "placeholder", "shots": len(clips)}
