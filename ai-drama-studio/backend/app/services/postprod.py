"""后期制作：把一堆 AI 生成的零散片段剪成一条能看的成片。

AI 生视频接口的产物有三个通病，直接 `ffmpeg -c copy` 拼起来必然出问题：
1. 分辨率/帧率/SAR 各不相同 → 拼接处跳变、时长对不上；
2. 没有音轨 → 成片没声音，观感直接崩；
3. 没有字幕 → 短剧没有字幕等于没做完。

这里统一做四件事：**归一化 → 烧字幕 → 贴配音 → 转场拼接 → 垫 BGM**。
全部是 CPU 侧的 ffmpeg 操作，本机（MX110 / 无 GPU）也能跑。

关键实现选择
------------
* **逐片段重编码**再拼接，而不是 concat demuxer + `-c copy`。
  copy 拼接要求所有片段编码参数完全一致，AI 产物做不到。
* **竖屏用 contain + 模糊背景**（而不是直接裁）。16:9 素材裁成 9:16 会丢掉
  大半画面，模糊垫底能保住构图，观感接近原生竖屏。
* **字幕用 libass `subtitles` 滤镜**（支持中文），逐片段单独烧，
  避免全局时间轴算错。字体找不到时自动降级为"不烧字幕"而不是让整条流水线挂掉。
* **镜头时长跟随配音时长**：配音比画面长就冻结尾帧，比画面短就截断。
  短剧节奏靠配音驱动，这样音画永远不会错位。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")

# 常见的中文字体候选（Windows / macOS / Linux），按顺序探测
CJK_FONT_CANDIDATES = [
    "Microsoft YaHei", "Microsoft YaHei UI", "SimHei", "DengXian",
    "PingFang SC", "Heiti SC", "Noto Sans CJK SC", "Source Han Sans SC",
    "WenQuanYi Zen Hei",
]


class PostProdError(RuntimeError):
    """后期制作失败。"""


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def _text(value) -> str:
    """asyncio 的 communicate() 在不同环境可能返回 bytes 或 str，统一成 str。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return value or ""


async def _run(args: Sequence[str], *, cwd: Optional[str] = None,
               timeout: float = 3600) -> str:
    """执行 ffmpeg（写文件的命令，不需要读 stdout）。

    stdout 走 DEVNULL、stderr 落到临时文件，而不是两个 PIPE：Windows 上
    asyncio 的 PIPE 会开 reader 线程按系统 gbk 解码，遇到非 ASCII 字节直接抛
    UnicodeDecodeError，把 ffmpeg 的错误信息全吞掉，排查时只剩一句「失败了」。
    """
    import tempfile

    with tempfile.TemporaryFile(mode="w+b") as errf:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL, stderr=errf,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise PostProdError(f"ffmpeg 超时（{timeout}s）：{' '.join(args[:12])}")
        if proc.returncode != 0:
            errf.seek(0)
            err = errf.read().decode("utf-8", "replace")
            raise PostProdError(
                f"ffmpeg 失败（code {proc.returncode}）：{err[-900:]}"
            )
    return ""


def _fps_value(rate: str) -> float:
    """'30000/1001' -> 29.97"""
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(rate)
    except Exception:
        return 0.0


async def probe(path: str) -> Dict[str, Any]:
    """读取媒体信息：时长/宽高/帧率/是否有音轨。"""
    args = [FFPROBE, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path)]
    import tempfile

    # stdout 也落临时文件：Windows 上 PIPE 会被 asyncio 按系统 gbk 解码，
    # 而 ffprobe 的 JSON 里带着中文路径，一解码就抛 UnicodeDecodeError，
    # 结果整个 JSON 拿不到、时长变成 0。自己按 utf-8 读最稳。
    with tempfile.TemporaryFile(mode="w+b") as outf, \
            tempfile.TemporaryFile(mode="w+b") as errf:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=outf, stderr=errf)
        await proc.communicate()
        if proc.returncode != 0:
            errf.seek(0)
            raise PostProdError(
                f"ffprobe 失败：{errf.read().decode('utf-8', 'replace')[-400:]}")
        outf.seek(0)
        raw = outf.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = 0.0
    for src in (video, data.get("format") or {}):
        try:
            if src.get("duration"):
                duration = float(src["duration"])
                break
        except (TypeError, ValueError):
            continue
    return {
        "path": str(path),
        "duration": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(_fps_value(str(video.get("r_frame_rate") or "0/1")), 3),
        "has_audio": bool(audio),
        "video_codec": video.get("codec_name", ""),
    }


_FONT_PATHS = {
    "Microsoft YaHei": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
                        r"C:\Windows\Fonts\msyhl.ttc"],
    "SimHei": [r"C:\Windows\Fonts\simhei.ttf"],
    "DengXian": [r"C:\Windows\Fonts\Deng.ttf", r"C:\Windows\Fonts\Dengb.ttf"],
    "PingFang SC": ["/System/Library/Fonts/PingFang.ttc",
                    "/System/Library/Fonts/STHeiti Medium.ttc"],
    "Noto Sans CJK SC": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                         "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"],
    "WenQuanYi Zen Hei": ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"],
}


def _detect_cjk_font() -> Optional[str]:
    """找出系统里可用的中文字体名（libass 用名）；找不到返回 None。"""
    for name in CJK_FONT_CANDIDATES:
        for p in _FONT_PATHS.get(name, []):
            if Path(p).exists():
                return name
    for p in Path(r"C:\Windows\Fonts").glob("msyh*"):
        return "Microsoft YaHei"
    return None


def _detect_cjk_font_path() -> Optional[str]:
    """找出中文字体的文件路径（PIL 画片头片尾用，必须给真实字体文件）。"""
    for name in CJK_FONT_CANDIDATES:
        for p in _FONT_PATHS.get(name, []):
            if Path(p).exists():
                return p
    for p in sorted(Path(r"C:\Windows\Fonts").glob("msyh*.tt*")):
        return str(p)
    return None


# --------------------------------------------------------------------------- #
# 字幕
# --------------------------------------------------------------------------- #

def srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:  # 四舍五入进位
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: Sequence[Dict[str, Any]], path: Path, width: int = 14) -> str:
    """cues: [{start, end, text}] → SRT 文件。返回路径。"""
    lines: List[str] = []
    for i, cue in enumerate(cues, 1):
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        start = float(cue.get("start") or 0)
        end = float(cue.get("end") or start + 3)
        if end <= start:
            end = start + 1.0
        lines.append(str(i))
        lines.append(f"{srt_time(start)} --> {srt_time(end)}")
        # 长台词折行，避免超出画面宽度
        lines.append(_wrap_cjk(text, width=width))
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _wrap_cjk(text: str, width: int = 16) -> str:
    """按字符数折行（中文没有空格，不能按 word wrap）。"""
    out, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= width or ch in "。！？；，、":
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return "\n".join(out[:3])  # 最多三行，防止遮挡画面


# --------------------------------------------------------------------------- #
# 成片组装
# --------------------------------------------------------------------------- #

@dataclass
class Segment:
    """一个镜头：画面 + 可选配音 + 可选字幕。"""
    video: str
    subtitle: str = ""
    voice: Optional[str] = None
    title: str = ""

    def __post_init__(self):
        self.video = str(self.video)


@dataclass
class BuildOptions:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    fit: str = "contain"          # contain(模糊垫底) | cover(裁切) | stretch
    burn_subtitle: bool = True
    font: Optional[str] = None
    font_size: Optional[int] = None
    subtitle_margin_v: int = 0
    crossfade: float = 0.3        # 0 = 硬切
    crf: int = 20
    preset: str = "veryfast"
    bgm: Optional[str] = None
    bgm_gain_db: float = -20.0
    # True = 音乐垫底（不够长就循环铺满）；False = 原片音轨（魔改线），
    # 只铺到自己结束，后面留静 —— 把台词循环播第二遍比没音轨更难听。
    bgm_loop: bool = True
    voice_gain_db: float = 2.0
    keep_temp: bool = False

    # 片头 / 片尾 / 角标
    title_card: Optional[str] = None       # 片头主标题（空则不做片头）
    title_card_sub: Optional[str] = None   # 片头副标题（如「第 1 集」）
    end_card: Optional[str] = None         # 片尾主文案（如「未完待续」）
    end_card_sub: Optional[str] = None     # 片尾副文案（如「关注我，看后续」）
    card_seconds: float = 2.5
    logo: Optional[str] = None             # 角标图片（本地路径，PNG 透明底最佳）
    logo_pos: str = "top-right"            # top-right / top-left / bottom-right / bottom-left
    logo_scale: float = 0.14               # 相对画面宽度
    logo_opacity: float = 0.85
    # 抹掉源视频右下角的「AI生成」角标。智谱 cogvideox 的图生视频接口
    # 无视 watermark_enabled=false，官方角标一定会烙进画面，只能后期擦。
    # 用 delogo 做邻域插值，比直接裁掉那一块更保构图。
    trim_badge: bool = False


def _badge_filter(src_w: int, src_h: int) -> str:
    """抹掉右下角「AI生成」角标的 delogo 参数（像素值）。"""
    if src_w <= 0 or src_h <= 0:
        return ""
    bw = max(8, int(src_w * 0.115))
    bh = max(6, int(src_h * 0.10))
    x = max(0, src_w - bw - max(2, int(src_w * 0.004)))
    y = max(0, src_h - bh - max(2, int(src_h * 0.006)))
    return f"delogo=x={x}:y={y}:w={bw}:h={bh},"


def _fit_filter(w: int, h: int, fit: str) -> str:
    if fit == "cover":
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if fit == "stretch":
        return f"scale={w}:{h}"
    # contain：原片等比缩放居中，背景用同帧放大模糊填充
    return (
        f"split=2[fg][bgsrc];"
        f"[bgsrc]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"gblur=sigma=22,eq=brightness=-0.08:saturation=0.7[bg];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
        f"[bg][fgs]overlay=(W-w)/2:(H-h)/2"
    )


async def _render_segment(seg: Segment, dst: Path, opts: BuildOptions,
                          workdir: Path, index: int) -> Dict[str, Any]:
    """把单个镜头渲染成规格统一的中间片段（含字幕与配音）。"""
    # ffmpeg 以 workdir 为 cwd 执行，相对路径会找不到文件，这里统一转绝对路径
    src = str(Path(seg.video).resolve())
    voice = str(Path(seg.voice).resolve()) if seg.voice and Path(seg.voice).exists() else None

    info = await probe(src)
    v_dur = info["duration"] or 5.0

    # 镜头时长跟随配音：配音长就冻结尾帧，短就截断
    a_dur = (await probe(voice))["duration"] if voice else 0.0
    target = round(max(v_dur, a_dur + 0.4) if a_dur else v_dur, 2)

    vf = ""
    if opts.trim_badge:
        # delogo 的 x/y/w/h 只吃整数，不支持 iw/ih 表达式，必须按源分辨率算好
        vf += _badge_filter(info.get("width") or 0, info.get("height") or 0)
    vf += _fit_filter(opts.width, opts.height, opts.fit)
    if v_dur + 0.05 < target:
        vf += f",tpad=stop_mode=clone:stop_duration={round(target - v_dur, 2):.2f}"
    vf += f",fps={opts.fps},setsar=1,format=yuv420p"

    font = opts.font or _detect_cjk_font()
    if opts.burn_subtitle and seg.subtitle and font:
        size = opts.font_size or max(24, int(opts.height * 0.032))
        # 每行字数按「画面宽度 ÷ 字号」算，保证长台词不超出画面
        cols = max(6, int(opts.width * 0.92 / max(size, 1)))
        srt_name = f"sub_{index:03d}.srt"
        write_srt([{"start": 0, "end": target, "text": seg.subtitle}],
                  workdir / srt_name, width=cols)
        # PlayResX/Y 必须显式给：SRT 没有 ASS 头，libass 默认按 384x288 解释
        # FontSize，再放大到实际分辨率，结果字号会大得离谱（占满半个屏幕）。
        style = (
            f"FontName={font},FontSize={size},"
            f"PlayResX={opts.width},PlayResY={opts.height},"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&HA0000000,"
            f"BorderStyle=1,Outline=3,Shadow=1,"
            f"Alignment=2,MarginV={opts.subtitle_margin_v or int(opts.height * 0.08)},"
            f"MarginL=24,MarginR=24"
        )
        vf += f",subtitles={srt_name}:force_style='{style}'"

    args: List[str] = [FFMPEG, "-y", "-i", src]
    if voice:
        args += ["-i", voice]
        af = (f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,"
              f"volume={opts.voice_gain_db}dB,apad,atrim=0:{target}[a]")
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        af = f"[1:a]atrim=0:{target}[a]"

    args += [
        "-filter_complex", f"[0:v]{vf}[v];{af}",
        "-map", "[v]", "-map", "[a]",
        "-t", str(target),
        "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-movflags", "+faststart",
        str(dst),
    ]
    await _run(args, cwd=str(workdir), timeout=1800)
    # 以实际文件时长为准：-t 与 VFR 输入组合时会有零点几秒偏差，
    # 而它直接决定 xfade 的 offset，累计起来会把片尾挤掉。
    try:
        actual = (await probe(str(dst)))["duration"]
        if actual:
            target = round(actual, 3)
    except Exception:  # noqa: BLE001
        pass
    return {"path": str(dst), "duration": target, "source": seg.video}


async def _join(clips: List[Dict[str, Any]], dest: Path, opts: BuildOptions) -> None:
    """把中间片段拼成一条；crossfade>0 时用 xfade 做软转场，失败自动回退硬拼。"""
    if len(clips) == 1:
        shutil.copyfile(clips[0]["path"], dest)
        return

    if opts.crossfade > 0:
        try:
            await _xfade(clips, dest, opts)
            return
        except PostProdError:
            pass  # 转场失败不影响出片，回退到硬拼

    list_file = dest.parent / f"_concat_{dest.stem}.txt"
    list_file.write_text(
        "".join(f"file '{Path(c['path']).as_posix()}'\n" for c in clips), encoding="utf-8"
    )
    await _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", "-movflags", "+faststart", str(dest)])


async def _xfade(clips: List[Dict[str, Any]], dest: Path, opts: BuildOptions) -> None:
    """xfade 链式转场。只处理视频流，音轨随后单独拼回。

    xfade 要求两路输入的 timebase 完全一致，否则直接报
    "First input link ... timebase does not match"。不同来源的片段
    （卡片、AI 生成、不同 fps）timebase 必然不同，所以每条输入先
    ``settb=AVTB`` 归一化，否则转场永远失败、只能退回硬拼。
    """
    d = max(0.05, min(opts.crossfade, 1.0))

    inputs: List[str] = []
    for c in clips:
        inputs += ["-i", c["path"]]

    n = len(clips)
    # xfade 要求所有输入的 timebase 和帧率完全一致，否则直接报
    # "does not match"。卡片/AI 片段来源各异，必须先归一化，不然转场永远失败、
    # 只能悄悄退回硬拼（还看不出错）。
    norm = ";".join(
        f"[{i}:v]settb=AVTB,fps={opts.fps},setpts=PTS-STARTPTS[n{i}]" for i in range(n)
    )

    parts: List[str] = [norm]
    offset = 0.0
    prev = "n0"
    for i in range(1, n):
        offset += max(0.2, clips[i - 1]["duration"] - d)
        out = f"xv{i}"
        parts.append(
            f"[{prev}][n{i}]xfade=transition=fade:duration={d}:offset={offset:.3f}[{out}]"
        )
        prev = out
    graph = ";".join(parts)

    silent = dest.parent / f"_xfade_silent_{dest.stem}.mp4"
    await _run([FFMPEG, "-y"] + inputs + [
        "-filter_complex", graph,
        "-map", f"[{prev}]",
        "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-an", str(silent),
    ], timeout=1800)

    # 音轨单独拼（acrossfade 对无音轨片段不友好，concat + 重编码更稳）
    parts_a = "".join(f"[{i}:a]anull[a{i}];" for i in range(n))
    concat_in = "".join(f"[a{i}]" for i in range(n))
    mixed = dest.parent / f"_xfade_mixed_{dest.stem}.mp4"
    await _run([FFMPEG, "-y"] + inputs + [
        "-i", str(silent),
        "-filter_complex", f"{parts_a}{concat_in}concat=n={n}:v=0:a=1[a]",
        "-map", f"{n}:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
        "-movflags", "+faststart", str(mixed),
    ], timeout=1800)
    shutil.move(str(mixed), str(dest))
    try:
        silent.unlink()
    except OSError:
        pass


async def _mix_bgm(video: Path, bgm: str, dest: Path, opts: BuildOptions) -> Path:
    """给成片垫 BGM：循环铺满 + 压低音量 + 结尾淡出。

    bgm_loop=False 时不循环（原片音轨走这条）：播完就静音收尾，
    否则成片比源视频长的时候会听见台词重播一遍。
    """
    info = await probe(str(video))
    total = max(1.0, info["duration"])
    out = dest
    bed = "aloop=loop=-1:size=2e9," if opts.bgm_loop else ""
    args = [
        FFMPEG, "-y", "-i", str(video), "-i", str(bgm),
        "-filter_complex",
        f"[1:a]{bed}atrim=0:{total:.2f},"
        f"volume={opts.bgm_gain_db}dB,afade=t=out:st={max(0.0, total - 2.0):.2f}:d=2[b];"
        f"[0:a][b]amix=inputs=2:duration=first:dropout_transition=0:weights=1 1[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out),
    ]
    await _run(args, timeout=1800)
    return out


def _draw_card(path: Path, title: str, sub: Optional[str], opts: "BuildOptions") -> None:
    """用 PIL 画片头/片尾卡。

    为什么不用 drawtext：它在 Windows 上要传 fontfile，且中文换行/居中都要手动算；
    PIL 直接出图更可控，也更稳。
    """
    from PIL import Image, ImageDraw, ImageFont

    w, h = opts.width, opts.height
    img = Image.new("RGB", (w, h), "#0b0b0b")
    draw = ImageDraw.Draw(img)

    # 竖向渐变，避免纯黑死板
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(11 + t * 26)
        g = int(11 + t * 24)
        b = int(16 + t * 48)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    font_path = _detect_cjk_font_path()
    main_size = int(h * 0.055)
    sub_size = int(h * 0.03)

    def _font(size: int):
        if font_path:
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    f_main, f_sub = _font(main_size), _font(sub_size)

    def _center(text: str, font, y: int, fill: str):
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((w - (box[2] - box[0])) / 2 - box[0], y), text, font=font, fill=fill)

    lines = [ln for ln in str(title or "").split("\n") if ln.strip()][:3]
    total = len(lines) * main_size * 1.5
    y = (h - total) / 2 - (h * 0.05 if sub else 0)
    for line in lines:
        _center(line, f_main, y, "#ffffff")
        y += main_size * 1.5

    if sub:
        _center(str(sub), f_sub, y + h * 0.02, "#9ca3af")
        # 副标题上方一条细分隔线，纯装饰
        ly = y + h * 0.008
        draw.line([(w * 0.38, ly), (w * 0.62, ly)], fill="#6366f1", width=max(2, int(h * 0.003)))

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")


async def _render_card(title: str, sub: Optional[str], dest: Path,
                       opts: "BuildOptions", workdir: Path) -> Dict[str, Any]:
    """卡片 → 带淡入淡出和静音轨的短视频片段。"""
    png = workdir / f"{dest.stem}.png"
    await asyncio.to_thread(_draw_card, png, title, sub, opts)
    sec = max(1.0, float(opts.card_seconds))
    fade_out_st = max(0.0, sec - 0.5)
    await _run([
        FFMPEG, "-y",
        "-loop", "1", "-i", str(png),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        # 必须显式 fps：`-loop 1` 的图片输入默认 25fps，和 30fps 的镜头片段
        # 混在一起会让 concat 的时长算错（多出两秒多），xfade 也会直接报帧率不匹配。
        "-vf", (f"fade=t=in:st=0:d=0.35,fade=t=out:st={fade_out_st:.2f}:d=0.45,"
                f"fps={opts.fps},setsar=1,format=yuv420p"),
        "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf),
        "-c:a", "aac", "-b:a", "128k", "-t", f"{sec:.2f}", "-shortest",
        str(dest),
    ], timeout=300)
    try:
        actual = (await probe(str(dest)))["duration"]
        if actual:
            sec = round(actual, 3)
    except Exception:  # noqa: BLE001
        pass
    return {"path": str(dest), "duration": sec, "source": "card"}


_LOGO_POSITIONS = {
    "top-left": "24:24",
    "top-right": "W-w-24:24",
    "bottom-left": "24:H-h-24",
    "bottom-right": "W-w-24:H-h-24",
}


async def _overlay_logo(video: Path, logo: str, opts: "BuildOptions") -> None:
    """整条成片叠一个角标。放在最后一步做，只重编码一次。"""
    pos = _LOGO_POSITIONS.get(str(opts.logo_pos).lower(), _LOGO_POSITIONS["top-right"])
    width = max(24, int(opts.width * float(opts.logo_scale)))
    alpha = max(0.05, min(1.0, float(opts.logo_opacity)))
    tmp = video.with_name(f"_logo_{video.name}")
    await _run([
        FFMPEG, "-y", "-i", str(video), "-i", str(logo),
        "-filter_complex",
        f"[1:v]format=rgba,scale={width}:-1,colorchannelmixer=aa={alpha}[lg];"
        f"[0:v][lg]overlay={pos}[out]",
        "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf),
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart", str(tmp),
    ], timeout=1800)
    shutil.move(str(tmp), str(video))


async def build_video(
    segments: Sequence[Segment],
    dest: str,
    options: Optional[BuildOptions] = None,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    """把若干镜头组装成一条成片。返回 {path, duration, width, height, size, clips}。"""
    opts = options or BuildOptions()
    segs = [s for s in segments if s.video and Path(s.video).exists()]
    if not segs:
        raise PostProdError("没有可用的视频片段，无法合成成片")

    dest_path = Path(dest).resolve()  # 后续 ffmpeg 都以 workdir 为 cwd，路径必须绝对
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = dest_path.parent / f"_tmp_{dest_path.stem}"
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    def _tick(pct: int, msg: str):
        if on_progress:
            try:
                on_progress(pct, msg)
            except Exception:
                pass

    try:
        clips: List[Dict[str, Any]] = []

        if opts.title_card:
            _tick(3, "生成片头")
            clips.append(await _render_card(
                opts.title_card, opts.title_card_sub,
                workdir / "card_open.mp4", opts, workdir))

        for i, seg in enumerate(segs):
            _tick(int(i / len(segs) * 70), f"渲染镜头 {i + 1}/{len(segs)}")
            clips.append(await _render_segment(
                seg, workdir / f"clip_{i:03d}.mp4", opts, workdir, i))

        if opts.end_card:
            _tick(72, "生成片尾")
            clips.append(await _render_card(
                opts.end_card, opts.end_card_sub,
                workdir / "card_end.mp4", opts, workdir))

        _tick(75, "拼接片段")
        joined = dest_path if not opts.bgm else dest_path.with_name(
            dest_path.stem + "_novbgm.mp4")
        await _join(clips, joined, opts)

        if opts.bgm and Path(opts.bgm).exists():
            _tick(88, "混入背景音乐")
            await _mix_bgm(joined, opts.bgm, dest_path, opts)
            try:
                joined.unlink()
            except OSError:
                pass

        if opts.logo and Path(opts.logo).exists():
            _tick(92, "叠加角标")
            await _overlay_logo(dest_path, str(Path(opts.logo).resolve()), opts)
    finally:
        if not opts.keep_temp and workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)

    _tick(95, "校验成片")
    info = await probe(str(dest_path))
    _tick(100, "完成")
    return {
        "path": str(dest_path),
        "duration": info["duration"],
        "width": info["width"],
        "height": info["height"],
        "size": dest_path.stat().st_size if dest_path.exists() else 0,
        "clips": len(clips),
    }


async def extract_tail_frame(video: str, dest: str, offset: float = 0.2) -> Optional[str]:
    """抽视频的最后一帧，存成图片。

    用途：镜头首尾帧续接 —— 上一镜的尾帧给下一镜做视觉参考，
    人物状态/光影/色调能接得上，成片不会一镜一世界。
    """
    src = Path(video)
    if not src.exists():
        return None
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    def _grab() -> bool:
        proc = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-sseof", f"-{offset}", "-i", str(src.resolve()),
             "-frames:v", "1", "-q:v", "2", str(dest_path.resolve())],
            capture_output=True, timeout=120,
        )
        return proc.returncode == 0 and dest_path.exists() and dest_path.stat().st_size > 0

    ok = await asyncio.to_thread(_grab)
    if ok:
        return str(dest_path)
    # 极短视频（不足 offset 秒）时 -sseof 会取不到帧，退化成取第一帧
    def _grab_first() -> bool:
        proc = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-i", str(src.resolve()),
             "-frames:v", "1", "-q:v", "2", str(dest_path.resolve())],
            capture_output=True, timeout=120,
        )
        return proc.returncode == 0 and dest_path.exists() and dest_path.stat().st_size > 0

    return str(dest_path) if await asyncio.to_thread(_grab_first) else None


def ffmpeg_ready() -> tuple[bool, str]:
    """ffmpeg / ffprobe 在不在 PATH 上（或 FFMPEG_BIN 指到了哪）。

    报错必须说清"去哪配"，因为成片/抽帧/魔改三条线全部依赖它，
    而 Windows 上它默认就是没有。
    """
    missing = [b for b, p in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE))
               if not shutil.which(p)]
    if missing:
        return False, ("找不到 " + "、".join(missing) +
                       "。装上它，或在 .env 里写 FFMPEG_BIN / FFPROBE_BIN 指到绝对路径。")
    return True, ""


async def extract_frames(video: str, dest_dir: str, count: int = 4,
                         max_side: int = 0, at: Optional[Sequence[float]] = None
                         ) -> List[Dict[str, Any]]:
    """从一条视频里抽若干关键帧，返回 [{path, time}]（按时间排序）。

    两种给法：``at=[0.4, 3.1]`` 精确到秒；或 ``count=4`` 在整片里均匀取
    （每段的中点，不是端点 —— 端点经常是黑场或转场）。

    帧是"喂给多模态模型的眼睛"：模型看不了视频文件，只能看几张图，
    所以抽帧的密度和时机决定了反推出来的分镜准不准。
    """
    src = Path(video)
    if not src.exists():
        raise PostProdError(f"抽帧失败：源视频不存在 {src}")
    ok, why = ffmpeg_ready()
    if not ok:
        raise PostProdError(why)

    out = Path(dest_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta = await probe(str(src))
    duration = float(meta.get("duration") or 0)

    times: List[float] = [float(t) for t in (at or [])]
    if not times:
        n = max(1, min(int(count or 4), 12))
        times = [(i + 0.5) * duration / n for i in range(n)] if duration > 0 else [0.0]
    if duration > 0:
        times = [min(max(t, 0.0), max(duration - 0.05, 0.0)) for t in times]

    scale = (f",scale={int(max_side)}:{int(max_side)}:force_original_aspect_ratio=decrease"
             if max_side else "")
    frames: List[Dict[str, Any]] = []
    for i, t in enumerate(times):
        dest = out / f"frame_{i + 1:02d}_{round(t, 2)}s.jpg"

        def _grab(dest=dest, t=t, scale=scale) -> bool:
            proc = subprocess.run(
                [FFMPEG, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src.resolve()),
                 "-frames:v", "1", "-q:v", "2", "-vf", f"fps=1{scale}",
                 str(dest.resolve())],
                capture_output=True, timeout=180,
            )
            return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0

        if await asyncio.to_thread(_grab):
            frames.append({"path": str(dest), "time": round(t, 3)})
    return frames


async def extract_audio(video: str, dest: str) -> Optional[str]:
    """把原片音轨单独抽出来（魔改线要保留原台词节奏时用）。没音轨返回 None。"""
    src = Path(video)
    if not src.exists():
        raise PostProdError(f"抽音轨失败：源视频不存在 {src}")
    ok, why = ffmpeg_ready()
    if not ok:
        raise PostProdError(why)
    meta = await probe(str(src))
    if not meta.get("has_audio"):
        return None
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    def _grab() -> bool:
        proc = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-i", str(src.resolve()),
             "-vn", "-ac", "2", "-ar", "44100", "-c:a", "aac", "-b:a", "160k",
             str(dest_path.resolve())],
            capture_output=True, timeout=300,
        )
        return proc.returncode == 0 and dest_path.exists() and dest_path.stat().st_size > 0

    return str(dest_path) if await asyncio.to_thread(_grab) else None


async def cut_clip(video: str, dest: str, start: float, end: float) -> Optional[str]:
    """按秒切出一段（魔改线里"只改这 8 秒"的片段级操作）。"""
    src = Path(video)
    if not src.exists():
        raise PostProdError(f"切片失败：源视频不存在 {src}")
    ok, why = ffmpeg_ready()
    if not ok:
        raise PostProdError(why)
    if float(end) <= float(start):
        return None
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    def _cut() -> bool:
        proc = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-ss", f"{float(start):.3f}",
             "-to", f"{float(end):.3f}", "-i", str(src.resolve()),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-movflags", "+faststart", str(dest_path.resolve())],
            capture_output=True, timeout=600,
        )
        return proc.returncode == 0 and dest_path.exists() and dest_path.stat().st_size > 0

    return str(dest_path) if await asyncio.to_thread(_cut) else None

