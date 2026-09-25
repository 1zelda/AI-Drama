"""水印消除层 — LaMa 修复模型（本地 CPU 可跑）+ 供应商固定角标预设。

设计要点：
1. AI 供应商的角标（如智谱 cogview 的「AI生成」）位置固定 → 用预设 region 直接修复，
   不需要逐帧检测；新供应商加一个 preset 即可。
2. 只对角标区域裁剪后送 LaMa，不动整帧 —— CPU 上 5 秒视频约几十秒即可处理完。
3. 处理后用 ffmpeg 无损回贴 + 重编码。

模型权重：backend/models/big-lama.pt（torch.jit，196MB）
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "big-lama.pt"
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")

# 供应商角标预设：
#   mode="text" —— 区域内只修「白字」像素（亮度阈值+膨胀），底纹/画面保留，修复最自然（首选）
#   mode="rect" —— 整块矩形区域交给 LaMa 重建（底纹复杂时可能发灰，兜底用）
WATERMARK_PRESETS: Dict[str, Dict[str, Any]] = {
    # 智谱 cogview 图片：右下角「AI生成」白字直接印在画面上（无底板）→ 白字检测
    "zhipu": {"regions": [(0.83, 0.87, 1.0, 1.0)], "mode": "text"},
    # 智谱 cogvideox 视频：白字 + 半透明黑色圆角底板 → 整块药丸重建（需大上下文，模块自动外扩）
    "zhipu_video": {"regions": [(0.815, 0.885, 1.0, 1.0)], "mode": "rect"},
    # Agnes 视频水印（720P 实测暂未见角标；如出现按此区域修）
    "agnes": {"regions": [(0.84, 0.89, 0.995, 0.995)], "mode": "text"},
}


class _Lama:
    _instance = None
    _model = None
    _device = None

    @classmethod
    def get(cls):
        if cls._model is None:
            import io

            import torch

            if not MODEL_PATH.exists():
                raise RuntimeError(
                    f"LaMa 权重不存在: {MODEL_PATH}。下载 "
                    "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt"
                )
            # torch JIT 的 C++ fopen 不支持非 ASCII 路径，改走字节流
            buf = io.BytesIO(MODEL_PATH.read_bytes())
            cls._model = torch.jit.load(buf, map_location="cpu")
            cls._model.eval()
            # GPU 加速：有可用 CUDA 就上（2GB 也够，角标区域是小裁剪推理）
            if torch.cuda.is_available():
                cls._model = cls._model.cuda().eval()
                cls._device = "cuda"
            else:
                cls._device = "cpu"
        return cls._model

    @classmethod
    def device(cls) -> str:
        cls.get()
        return cls._device or "cpu"

    @classmethod
    def inpaint(cls, image_rgb: "np.ndarray", mask: "np.ndarray") -> "np.ndarray":
        """image_rgb: HxWx3 uint8; mask: HxW uint8 (255=要修复的区域)。返回修复后的 HxWx3。
        LaMa 要求尺寸为 8 的倍数，内部自动 reflect-pad 后裁回。"""
        import torch

        model = cls.get()
        h, w = image_rgb.shape[:2]
        ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
        if ph or pw:
            image_rgb = np.pad(image_rgb, ((0, ph), (0, pw), (0, 0)), mode="reflect")
            mask = np.pad(mask, ((0, ph), (0, pw)), mode="reflect")
        img = torch.from_numpy(image_rgb.astype(np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0)
        m = torch.from_numpy((mask > 127).astype(np.float32)).unsqueeze(0).unsqueeze(0)
        if cls._device == "cuda":
            img, m = img.cuda(), m.cuda()
        with torch.no_grad():
            result = model(img, m)
        out = result[0].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
        return (out[:h, :w] * 255).astype(np.uint8)


def regions_to_masks(width: int, height: int,
                     regions: List[Tuple[float, float, float, float]],
                     pad: float = 0.003) -> "np.ndarray":
    """相对区域 → 全帧 0/255 mask（带少量外扩）。"""
    mask = np.zeros((height, width), dtype=np.uint8)
    for x0, y0, x1, y1 in regions:
        px0 = max(0, int((x0 - pad) * width)); px1 = min(width, int((x1 + pad) * width))
        py0 = max(0, int((y0 - pad) * height)); py1 = min(height, int((y1 + pad) * height))
        mask[py0:py1, px0:px1] = 255
    return mask


def text_mask(crop_rgb: "np.ndarray", base_mask: "np.ndarray",
              lum_threshold: int = 200) -> "np.ndarray":
    """在区域内检测白色文字像素（高亮度、低饱和度），膨胀后与区域 mask 相交。"""
    import cv2

    hsv = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    white = ((v > lum_threshold) & (s < 90)).astype(np.uint8) * 255
    # 膨胀把字边和半透明底一起吃掉
    white = cv2.dilate(white, np.ones((5, 5), np.uint8), iterations=2)
    out = np.where(white > 0, base_mask, 0).astype(np.uint8)
    # 至少给文字像素一点膨胀余量；若检测不到（阈值不合适），退回整块 rect
    if (out > 0).sum() < 50:
        return base_mask
    return out


def static_badge_mask(frames_crops: List["np.ndarray"], idx: int, base_mask: "np.ndarray",
                      diff_threshold: int = 22) -> "np.ndarray":
    """时域中值法：固定角标在跨帧中值里「留下来」，流动画面被抹平。
    mask = |当前帧 - 中值帧| 超阈值处（限定在区域内）。对任意背景都稳，前提是机位固定。"""
    import cv2

    stack = np.stack([cv2.cvtColor(f, cv2.COLOR_RGB2GRAY) for f in frames_crops]).astype(np.float32)
    median = np.median(stack, axis=0)
    cur = stack[idx]
    diff = (np.abs(cur - median) > diff_threshold).astype(np.uint8) * 255
    diff = cv2.dilate(diff, np.ones((5, 5), np.uint8), iterations=2)
    return np.where(diff > 0, base_mask, 0).astype(np.uint8)


def process_video(video_path: str, regions: List[Tuple[float, float, float, float]],
                  output_path: Optional[str] = None, progress=None,
                  mode: str = "text") -> str:
    """对整段视频的指定区域做逐帧 LaMa 修复。regions 为相对坐标。
    mode: text=白字检测 | rect=整块重建 | static=时域中值（固定机位角标首选）"""
    import cv2

    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(video_path)
    output_path = output_path or str(src.with_name(src.stem + "_clean.mp4"))
    tmpdir = tempfile.mkdtemp(prefix="wm_")
    frames_dir = Path(tmpdir) / "f"
    out_frames = Path(tmpdir) / "o"
    frames_dir.mkdir(parents=True)
    out_frames.mkdir(parents=True)

    # 1) 抽帧
    subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(src), "-vf", "fps=30", str(frames_dir / "%06d.png")],
        check=True,
    )
    frames = sorted(frames_dir.glob("*.png"))
    if not frames:
        raise RuntimeError("抽帧失败")

    first = cv2.imread(str(frames[0]))
    h, w = first.shape[:2]
    mask = regions_to_masks(w, h, regions)
    ys, xs = np.where(mask > 0)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1

    # 关键：给 LaMa 足够的上下文——处理裁剪框围绕 mask 外接矩形外扩（小裁剪=填充质感差）
    bh, bw = y1 - y0, x1 - x0
    cy0 = max(0, y0 - int(bh * 1.5)); cy1 = min(h, y1 + int(bh * 1.5))
    cx0 = max(0, x0 - int(bw * 1.5)); cx1 = min(w, x1 + int(bw * 1.5))

    # static 模式：预载全部角标区域裁剪，算时域中值
    crops_gray = None
    if mode == "static":
        crops = []
        for f in frames:
            im = cv2.imread(str(f))
            crops.append(cv2.cvtColor(im[cy0:cy1, cx0:cx1], cv2.COLOR_BGR2RGB) if im is not None else np.zeros((1, 1, 3), np.uint8))
        crops_gray = crops

    import cv2  # noqa: F811

    for i, f in enumerate(frames):
        img = cv2.imread(str(f))
        if img is None:
            continue
        rgb = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        crop = rgb[cy0:cy1, cx0:cx1]
        crop_mask = np.zeros(crop.shape[:2], np.uint8)
        crop_mask[y0 - cy0:y1 - cy0, x0 - cx0:x1 - cx0] = mask[y0:y1, x0:x1]
        if mode == "text":
            crop_mask = text_mask(crop, crop_mask)
        elif mode == "static" and crops_gray:
            crop_mask = static_badge_mask(crops_gray, i, crop_mask)
        fixed = _Lama.inpaint(crop, crop_mask)
        rgb[cy0:cy1, cx0:cx1] = fixed
        cv2.imwrite(str(out_frames / f.name), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if progress:
            try:
                progress(int((i + 1) / len(frames) * 100))
            except Exception:
                pass

    # 3) 回拼视频（视频流重编码，音频直接拷贝）
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-framerate", "30", "-i", str(out_frames / "%06d.png"),
         "-i", str(src), "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy",
         output_path],
        check=True,
    )
    # 清理临时目录
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    return output_path


def remove_watermark(video_path: str, provider: Optional[str] = None,
                     regions: Optional[List[Tuple[float, float, float, float]]] = None,
                     output_path: Optional[str] = None, progress=None,
                     mode: Optional[str] = None) -> str:
    """入口：传 provider 用预设，或直接传 regions（相对坐标）。"""
    preset = WATERMARK_PRESETS.get((provider or "").lower()) if provider else None
    if regions:
        rs, m = regions, (mode or "text")
    elif preset:
        rs, m = preset["regions"], (mode or preset.get("mode", "text"))
    else:
        raise ValueError(f"未知供应商 {provider!r} 且未指定 regions；可用预设: {sorted(WATERMARK_PRESETS)}")
    return process_video(video_path, rs, output_path, progress, mode=m)
