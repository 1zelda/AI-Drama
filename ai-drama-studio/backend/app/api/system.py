"""系统自检 + 一键推荐通道。

设置页「一键自检」的数据来源。目标只有一个：让用户不用看日志、不用改 JSON，
点一下就知道「缺哪个 Key、该填什么、现在能不能出片」。
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Query

from ..services import provider_picker as pp

router = APIRouter(prefix="/api/system", tags=["system"])


def _ffmpeg() -> Dict[str, Any]:
    return {key: (shutil.which(key) or "") for key in ("ffmpeg", "ffprobe")}


def _ffmpeg_version() -> str:
    import subprocess

    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return out.stdout.decode("utf-8", "ignore").splitlines()[0][:80]
    except Exception as exc:  # noqa: BLE001
        return f"不可用：{exc}"


async def _check_llm() -> Dict[str, Any]:
    """真发一条极短请求，确认 Key 有效（比只看有没有填靠谱得多）。"""
    from ..llm.planner import llm_config

    cfg = llm_config()
    if not cfg["api_key"]:
        return {"ok": False, "detail": "未填写 LLM API Key（填一个智谱 Key 即可，GLM-4-Flash 免费）",
                "model": cfg["model"], "provider": cfg.get("provider", "")}
    try:
        from ..llm.planner import LAST_CALL, chat_completion

        # 用带 json_object 的请求实测：流水线里每个剧本节点都走 JSON 模式，
        # 只测普通对话会在真正跑流水线时才发现模型不支持。
        text = await asyncio.wait_for(
            chat_completion(
                messages=[{"role": "user", "content": '回复 JSON：{"reply":"正常"}'}],
                response_format={"type": "json_object"},
                max_tokens=16,
            ),
            timeout=30,
        )
        used = LAST_CALL.get("provider") or cfg.get("provider", "")
        return {"ok": True, "detail": f"连通 · {LAST_CALL.get('model') or cfg['model']} · 实际走 {used}",
                "model": cfg["model"], "provider": used, "echo": (text or "").strip()[:24]}
    except asyncio.TimeoutError:
        return {"ok": False, "detail": "请求超时（30s）", "model": cfg["model"]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:220],
                "model": cfg["model"], "provider": cfg.get("provider", "")}


async def _check_image(deep: bool) -> Dict[str, Any]:
    settings = pp.load_settings()
    available = pp.configured("image", settings)
    picked = pp.pick("image", None, settings)
    result: Dict[str, Any] = {
        "ok": bool(available),
        "available": available,
        "picked": picked.get("provider"),
        "detail": ("已配置：" + "、".join(available)) if available else pp.missing_hint("生图"),
    }
    if deep and picked.get("provider"):
        try:
            from ..providers.image_providers import get_image_provider
            import tempfile
            from pathlib import Path

            provider = get_image_provider(picked["provider"])
            with tempfile.TemporaryDirectory() as tmp:
                res = await provider.generate(
                    prompt="a single red apple on white background",
                    width=512, height=512,
                    output_dir=tmp, filename="probe",
                )
            result["ok"] = bool(res.paths or res.urls)
            result["detail"] = (
                f"{picked['provider']} 实测通过" if result["ok"]
                else f"{picked['provider']} 未返回图片"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"{picked['provider']} 实测失败：{type(exc).__name__}: {exc}"[:220]
    return result


async def _check_video(deep: bool) -> Dict[str, Any]:
    settings = pp.load_settings()
    available = pp.configured("video", settings)
    picked = pp.pick("video", None, settings)
    result: Dict[str, Any] = {
        "ok": bool(available),
        "available": available,
        "picked": picked.get("provider"),
        "detail": ("已配置：" + "、".join(available)) if available else pp.missing_hint("生视频"),
    }
    if deep:
        # 实调一次要几十秒到几分钟且消耗额度，默认不做，只提示如何在流水线里验证
        result["detail"] += "（视频通道不在自检里实调，跑一次 1 镜头的流水线即可验证）"
    return result


async def _check_comfyui() -> Dict[str, Any]:
    try:
        from ..providers.comfyui import ComfyUIClient

        client = ComfyUIClient()
        ok = await asyncio.to_thread(client.is_available)
        return {"ok": bool(ok), "url": client.server_url,
                "detail": "已连接" if ok else "未启动（本机无 GPU 可忽略，走云端通道）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:160]}


def _check_tts() -> Dict[str, Any]:
    try:
        import edge_tts  # noqa: F401

        return {"ok": True, "detail": "edge-tts 可用（免费，无需 Key）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"edge-tts 不可用：{exc}"}


def _check_font() -> Dict[str, Any]:
    from ..services.postprod import _detect_cjk_font

    font = _detect_cjk_font()
    return {"ok": bool(font), "font": font,
            "detail": f"字幕字体：{font}" if font else "未找到中文字体，字幕将跳过"}


# 这些模型名看得懂图；不在名单里的只提示，不下死结论（各家取名没有统一标准）
_VISION_HINTS = ("4v", "vl", "vision", "gpt-4o", "gemini", "kimi", "claude")


def _check_restyle() -> Dict[str, Any]:
    """片段魔改线（video-restyle）的专项就绪度，不参与「能不能出片」的判定。

    这条线比另外两条多吃三样东西：能看图的模型（逐镜反推）、源视频上传、
    以及只有「整段编辑」路线才需要的公网素材地址。缺哪样就点名哪样，
    否则用户只能在跑到一半时看到一句云端报错。
    """
    gaps: List[str] = []
    try:
        from ..llm.planner import vision_config

        vc = vision_config()
    except Exception:  # noqa: BLE001 - 读不到配置按「没配」处理
        vc = None
    if not vc:
        gaps.append("反推原画面要一个能看图的模型（VISION_API_KEY / ZHIPU_API_KEY）")
    else:
        model = str(vc.get("model") or "").lower()
        if model and not any(h in model for h in _VISION_HINTS):
            gaps.append(f"视觉模型名 {vc.get('model')} 看着不像多模态模型，"
                        "反推可能读不到画面（填 VISION_MODEL=gpt-4o 或 glm-4v-flash）")
    ds = pp.configured("video", pp.load_settings())
    if "dashscope" not in ds:
        gaps.append("整段编辑路线（video_edit）还需 DASHSCOPE_API_KEY，出厂关闭、不影响逐镜重绘")
    try:
        from ..providers.asset_host import get_asset_host

        if not get_asset_host().configured:
            gaps.append("PUBLIC_ASSET_BASE_URL 未配置：本机源视频传不上云端（逐镜重绘不受影响）")
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"公网素材地址检查失败：{type(exc).__name__}")

    return {"ok": not gaps, "vision_model": (vc or {}).get("model") or "",
            "detail": "魔改线就绪" if not gaps else "魔改线缺项：" + "；".join(gaps)}


@router.get("/ping")
async def ping():
    """极轻量探活，前端看门狗用它判断「后端还活着吗」。

    不带任何 provider 检查：长跑时每秒都要能问一次，不能因为自检慢而误报。
    """
    from ..orchestrator.events import bus

    return {"ok": True, "ts": time.time(), "active_runs": len(bus.list_runs(100))}


@router.get("/health")
async def health(deep: bool = Query(False, description="true 时实测生图通道（会消耗少量额度）")):
    llm, image, video, comfy = await asyncio.gather(
        _check_llm(), _check_image(deep), _check_video(deep), _check_comfyui()
    )
    ffmpeg = _ffmpeg()
    ffmpeg_ok = bool(ffmpeg.get("ffmpeg")) and bool(ffmpeg.get("ffprobe"))
    tts, font = _check_tts(), _check_font()

    checks = {
        "llm": llm,
        "image": image,
        "video": video,
        "tts": tts,
        "ffmpeg": {"ok": ffmpeg_ok, "detail": _ffmpeg_version() if ffmpeg_ok
                   else "未安装 ffmpeg/ffprobe，无法合成成片",
                   "paths": ffmpeg},
        "subtitle_font": font,
        "comfyui": comfy,
        # 只提示，不进 missing/degraded：出厂的逐镜重绘路线不需要公网隧道也能跑
        "restyle": _check_restyle(),
    }

    # 出片的最低要求：LLM + 生图 + 生视频 + ffmpeg。TTS 和字体缺失只降级不阻断。
    blocking = ["llm", "image", "video", "ffmpeg"]
    missing = [k for k in blocking if not checks[k]["ok"]]
    degraded = [k for k in ("tts", "subtitle_font") if not checks[k]["ok"]]

    if not missing and not degraded:
        summary = "全部就绪，可以直接一键成片"
    elif not missing:
        summary = "可以出片，但" + "、".join(
            {"tts": "没有配音", "subtitle_font": "字幕会跳过"}[k] for k in degraded)
    else:
        names = {"llm": "LLM", "image": "生图", "video": "生视频", "ffmpeg": "FFmpeg"}
        summary = "缺少关键配置：" + "、".join(names[k] for k in missing)

    return {
        "ok": not missing,
        "ready": not missing,
        "summary": summary,
        "missing": missing,
        "degraded": degraded,
        "checks": checks,
        "recommended": {
            "image": checks["image"].get("picked"),
            "video": checks["video"].get("picked"),
        },
    }


@router.post("/cleanup")
async def cleanup(body: Dict[str, Any]):
    """清理 output/ 里的中间产物（分镜图、单镜头视频、配音、临时目录）。

    成片库里登记过的视频和封面**永远不动**，剩下的才清。
    默认 ``dry_run=true`` 只出计划，前端先把「要删多少文件、多少 MB」摊给用户看，
    确认后再真删 —— 这类接口一旦误删是不可恢复的。
    """
    from ..api.history import OUTPUT_DIR, _load as load_history

    dry_run = bool(body.get("dry_run", True))
    keep_hours = float(body.get("keep_hours", 0) or 0)

    protected: set = set()
    for item in load_history():
        for key in ("video_path", "poster_path"):
            p = item.get(key)
            if p:
                protected.add(str(Path(p).resolve()).lower())

    cutoff = time.time() - keep_hours * 3600 if keep_hours > 0 else None
    files: List[Path] = []
    total = 0
    if OUTPUT_DIR.exists():
        for p in OUTPUT_DIR.rglob("*"):
            if not p.is_file():
                continue
            if "posters" in p.relative_to(OUTPUT_DIR).parts:
                continue
            if str(p.resolve()).lower() in protected:
                continue
            if cutoff is not None and p.stat().st_mtime > cutoff:
                continue
            files.append(p)
            total += p.stat().st_size

    # 空的 /_tmp_* 工作目录也一并清掉（postprod 正常会删，异常退出时会留下来）
    tmp_dirs = [d for d in OUTPUT_DIR.rglob("_tmp_*") if d.is_dir()] if OUTPUT_DIR.exists() else []

    samples = [
        p.relative_to(OUTPUT_DIR).as_posix() for p in sorted(files, key=lambda x: -x.stat().st_size)[:15]
    ]

    deleted = 0
    if not dry_run:
        for p in files:
            try:
                p.unlink()
                deleted += 1
            except OSError:
                pass
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        # 清掉空目录
        if OUTPUT_DIR.exists():
            for d in sorted(OUTPUT_DIR.rglob("*"), key=lambda x: -len(x.parts)):
                if d.is_dir() and not any(d.iterdir()):
                    try:
                        d.rmdir()
                    except OSError:
                        pass

    return {
        "dry_run": dry_run,
        "files": len(files),
        "deleted": deleted,
        "bytes": total,
        "mb": round(total / 1024 / 1024, 1),
        "tmp_dirs": len(tmp_dirs),
        "samples": samples,
    }


@router.get("/tts/voices")
async def tts_voices():
    """中文配音音色列表（edge-tts 免费音色）。"""
    try:
        import edge_tts

        voices = await edge_tts.list_voices()
    except Exception as exc:  # noqa: BLE001
        return {"voices": [], "error": str(exc)}
    zh = [
        {"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
        for v in voices if str(v.get("Locale", "")).startswith("zh-")
    ]
    return {"voices": zh}


@router.get("/providers")
async def providers():
    """各模态可用通道 + 缺哪个 Key，供设置页/一键成片页下拉。"""
    settings = pp.load_settings()
    return {
        "image": {"available": pp.configured("image", settings),
                  "picked": pp.pick("image", None, settings).get("provider")},
        "video": {"available": pp.configured("video", settings),
                  "picked": pp.pick("video", None, settings).get("provider")},
    }


@router.get("/restyle_presets")
async def restyle_presets():
    """片段魔改线的目标风格库（「假如这条片子是印度人/韩国人/八十年代港片拍的」）。

    /make 的下拉读它拿 id/label/category；选中后要把 preset_* 那几项原样当运行输入
    传进视频-restyle 工作流 —— 世界观圣经模板只认扁平键，不认 {{preset.xxx}}。
    整库是本地 JSON，不联网，读不到就返回空列表而不是 500（下拉空了比页面崩了好修）。
    """
    import json

    path = Path(__file__).parent.parent.parent / "config" / "restyle_presets.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"presets": [], "error": f"读不到 config/restyle_presets.json：{exc}"}
    return {"version": doc.get("version"), "note": doc.get("note"),
            "presets": doc.get("presets") or []}
