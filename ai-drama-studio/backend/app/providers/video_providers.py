"""Unified video generation providers.

Every provider returns the same `VideoResult`, so the workflow engine can treat
ComfyUI / Seedance / Kling / Agnes interchangeably and a node can switch tracks
by changing one string.

Two tracks
----------
* **local**  ``comfyui``  — Wan 2.2 I2V on your own 5060 Ti. Full control over the
  sampler, LoRA, frame count, and reference image.
* **cloud**  ``seedance`` / ``kling`` / ``agnes`` — no GPU needed. Seedance accepts
  reference frames as **base64 data URLs**, so unlike Agnes it does not require a
  public asset host.

Why base64 matters: the Agnes path aborts with
"首帧/尾帧/参考图需要公网 URL" unless you expose your filesystem to the internet.
Seedance just takes the bytes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

WORKFLOW_DIR = Path(__file__).parent.parent.parent / "config" / "comfyui_workflows"


@dataclass
class VideoResult:
    """Normalised result returned by every provider."""

    url: str = ""
    local_path: Optional[str] = None
    duration: float = 5.0
    format: str = "mp4"
    provider: str = ""
    task_id: Optional[str] = None
    status: str = "completed"
    # True 表示图生视频被拒、退回文生视频：画面不再跟随分镜图（通常是横屏默认尺寸）
    used_fallback: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "local_path": self.local_path,
            "duration": self.duration,
            "format": self.format,
            "provider": self.provider,
            "task_id": self.task_id,
            "status": self.status,
            "used_fallback": self.used_fallback,
        }


class BaseVideoProvider(ABC):
    """Common contract for video providers."""

    name = "base"
    # Set False for providers that can accept local files / base64 directly.
    needs_public_assets = False

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 5.0,
        **kwargs,
    ) -> VideoResult:
        ...

    def _require_key(self, env_var: str):
        key = self.api_key or os.getenv(env_var)
        if not key:
            raise ValueError(
                f"{self.name} is not configured: set {env_var} "
                "(or pass api_key explicitly)."
            )
        return key


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _as_data_url(value: str) -> str:
    """Local file -> ``data:<mime>;base64,...``. URLs and existing data URLs pass through."""
    if not value:
        return value
    if value.startswith(("http://", "https://", "data:")):
        return value
    path = Path(value)
    if not path.exists():
        # Not a file and not a URL — hand it over unchanged so the API can complain.
        return value
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def _shrink_reference_image(value: str, max_side: int = 1024) -> str:
    """本地图片 → 尽量小的 JPEG data URL。

    直接 base64 原图是有坑的：分镜图 768×1344 的 PNG 动辄 2~4MB，base64 后还要再
    胖 1/3，图生视频接口常常直接拒收，于是静默退回文生视频 —— 出来的画面和分镜图
    完全对不上，而且还是模型默认的横屏 1792×1024，成片只能靠模糊垫底。
    先按长边缩到 1024 再转 JPEG q85，一般能压到 200KB 以内，I2V 基本都能收。
    """
    if not value or value.startswith(("http://", "https://", "data:")):
        return value
    path = Path(value)
    if not path.exists():
        return value
    try:
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = max(w, h) / max_side
            if scale > 1:
                im = im.resize((max(1, int(w / scale)), max(1, int(h / scale))), Image.LANCZOS)
            import io

            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85, optimize=True)
        # 压完反而更大（极小图/异常图）就用回原图
        if buf.tell() < path.stat().st_size:
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass
    return _as_data_url(value)


async def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
    return dest


# --------------------------------------------------------------------------- #
# Local track: ComfyUI (Wan 2.2 I2V)
# --------------------------------------------------------------------------- #

class ComfyUIVideoProvider(BaseVideoProvider):
    """Render a shot through a ComfyUI video workflow (Wan 2.2 I2V by default).

    The reference frame is uploaded to ComfyUI's input dir first, then injected
    into the workflow via its ``params`` block or ``{{reference_image}}`` placeholder.
    """

    name = "comfyui"
    needs_public_assets = False

    def __init__(self, workflow_file: str = "image-to-video.json",
                 server_url: Optional[str] = None, output_node: Optional[str] = None, **_):
        super().__init__()
        self.workflow_file = workflow_file
        self.server_url = server_url
        self.output_node = output_node

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 5.0,
        *,
        negative_prompt: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        seed: Optional[int] = None,
        fps: Optional[int] = None,
        output_dir: Optional[str] = None,
        filename: str = "shot",
        timeout: float = 1800,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        **kwargs,
    ) -> VideoResult:
        from .comfyui import ComfyUIClient, ComfyUIError
        from .image_providers import _render_placeholders

        path = Path(self.workflow_file)
        if not path.is_absolute():
            path = WORKFLOW_DIR / path
        if not path.exists():
            raise FileNotFoundError(
                f"找不到 ComfyUI 视频工作流：{path}。可用：{[p.name for p in WORKFLOW_DIR.glob('*.json')]}"
            )

        client = ComfyUIClient(server_url=self.server_url, capability="video")
        if not client.is_available():
            raise ComfyUIError(
                f"ComfyUI 不可达（{client.server_url}）。请启动 ComfyUI，"
                "或把 COMFYUI_VIDEO_SERVER_URL 指向已装好的机器，或改用 provider: seedance。"
            )

        meta = ComfyUIClient.load_workflow_meta(path)
        graph = ComfyUIClient.load_workflow(path)

        uploaded: Dict[str, Any] = {}
        if reference_image and Path(reference_image).exists():
            name = client.upload_image(
                Path(reference_image), f"i2v_{uuid.uuid4().hex[:8]}{Path(reference_image).suffix}"
            )
            uploaded["reference_image"] = name
        # 口型同步（InfiniteTalk / MultiTalk 一类预设）：音频也丢进 ComfyUI 的 input 目录，
        # 预设里用 {{audio_file}} 占位交给 LoadAudio 节点。upload_image 只是往 input 目录
        # 传文件，不限图片，所以这里直接复用。
        audio = kwargs.get("audio")
        if audio and Path(str(audio)).exists():
            uploaded["audio_file"] = client.upload_image(
                Path(str(audio)), f"voice_{uuid.uuid4().hex[:8]}{Path(str(audio)).suffix}"
            )
        elif audio:
            raise FileNotFoundError(f"口型同步音频不存在：{audio}")

        # 16 fps * duration is the Wan convention for frame count.
        fps = fps or 16
        length = int(round(duration * fps))
        context: Dict[str, Any] = {
            "prompt": prompt,
            "positive_prompt": prompt,
            "negative_prompt": negative_prompt or "",
            "length": length,
            "duration": duration,
            "seed": seed if seed is not None else ComfyUIClient.random_seed(),
            "width": width or 832,
            "height": height or 480,
            "fps": fps,
        }
        context.update(uploaded)

        graph = _render_placeholders(graph, context)

        params: Dict[str, Dict[str, str]] = meta.get("params") or {}
        patches: Dict[str, Dict[str, Any]] = {}
        for key, value in context.items():
            mapping = params.get(key)
            if mapping and mapping.get("field"):
                patches.setdefault(str(mapping["node"]), {})[mapping["field"]] = value
        if patches:
            graph = ComfyUIClient.patch_workflow(graph, patches, strict=False)

        out_dir = Path(output_dir or "output/videos")
        out_dir.mkdir(parents=True, exist_ok=True)

        def _run():
            return client.generate(
                graph,
                str(self.output_node or meta.get("output_node") or "111"),
                out_dir / f"{filename}.mp4",
                timeout=timeout,
                on_progress=on_progress,
                preflight=True,
            )

        paths = await asyncio.to_thread(_run)
        local = str(paths[0]) if paths else None
        return VideoResult(
            url="",
            local_path=local,
            duration=duration,
            provider=self.name,
            status="completed",
            raw={"server": client.server_url, "workflow": path.stem},
        )


# --------------------------------------------------------------------------- #
# Cloud track: Seedance (火山方舟 Ark)
# --------------------------------------------------------------------------- #

class SeedanceProvider(BaseVideoProvider):
    """Doubao Seedance via the Volcengine Ark API.

    Docs: POST ``{base}/contents/generations/tasks`` -> ``{"id": "cgt-..."}``,
    then poll GET ``{base}/contents/generations/tasks/{id}`` until ``status`` is
    terminal, reading ``content.video_url``.

    Reference frames are sent as **base64 data URLs**, so no public asset host is
    needed. Three mutually exclusive modes:

    * ``first_frame`` — one image, role ``first_frame``
    * ``first_last_frame`` — two images, roles ``first_frame`` / ``last_frame``
    * ``reference`` — N images with no role (全模态参考生视频)
    """

    name = "seedance"
    needs_public_assets = False

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.base_url = (base_url or os.getenv("ARK_BASE_URL")
                         or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
        self.model = model or os.getenv("SEEDANCE_MODEL", "doubao-seedance-1-5-pro-251215")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 5.0,
        *,
        mode: str = "first_frame",
        last_frame: Optional[str] = None,
        images: Optional[List[str]] = None,
        resolution: str = "720p",
        ratio: Optional[str] = None,
        seed: Optional[int] = None,
        camera_fixed: bool = False,
        watermark: bool = True,
        generate_audio: bool = False,
        output_dir: Optional[str] = None,
        filename: str = "shot",
        max_wait: float = 900,
        on_progress: Optional[Callable[[int, str], None]] = None,
        **kwargs,
    ) -> VideoResult:
        key = self._require_key("ARK_API_KEY")

        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]

        if mode == "first_frame" and reference_image:
            content.append({
                "type": "image_url",
                "image_url": {"url": _as_data_url(reference_image)},
                "role": "first_frame",
            })
        elif mode == "first_last_frame" and reference_image and last_frame:
            content.append({
                "type": "image_url",
                "image_url": {"url": _as_data_url(reference_image)},
                "role": "first_frame",
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": _as_data_url(last_frame)},
                "role": "last_frame",
            })
        elif mode == "reference" and images:
            for img in images:
                content.append({"type": "image_url", "image_url": {"url": _as_data_url(img)}})

        has_image = any(c.get("type") == "image_url" for c in content)
        payload: Dict[str, Any] = {
            "model": self.model,
            "content": content,
            "duration": int(round(duration)),
            "camera_fixed": camera_fixed,
            "watermark": watermark,
        }
        # 2.5/2.0 force `adaptive` for frame-conditioned tasks; 1080p is not
        # allowed when reference images are present.
        payload["ratio"] = ratio or ("adaptive" if has_image else "16:9")
        if has_image and resolution == "1080p":
            resolution = "720p"
        payload["resolution"] = resolution
        if seed is not None:
            payload["seed"] = seed
        if generate_audio:
            payload["generate_audio"] = True

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/contents/generations/tasks", json=payload, headers=headers
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Seedance 提交失败（HTTP {resp.status_code}）: {resp.text[:500]}"
                )
            task_id = resp.json().get("id")

        if not task_id:
            raise RuntimeError(f"Seedance 未返回任务 ID: {resp.text[:300]}")

        # Poll. Video jobs are asynchronous and typically take 1-5 minutes.
        deadline = time.time() + max_wait
        status, video_url, last_payload = "", "", {}
        waited = 0
        while time.time() < deadline:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(
                    f"{self.base_url}/contents/generations/tasks/{task_id}", headers=headers
                )
                r.raise_for_status()
                last_payload = r.json()
            status = last_payload.get("status", "")
            if on_progress:
                try:
                    on_progress(min(int(waited / max(max_wait, 1) * 100), 95), status)
                except Exception:
                    pass
            if status == "succeeded":
                video_url = (last_payload.get("content") or {}).get("video_url", "")
                break
            if status in ("failed", "expired", "cancelled"):
                err = (last_payload.get("error") or {}).get("message") or json.dumps(
                    last_payload, ensure_ascii=False
                )[:400]
                raise RuntimeError(f"Seedance 任务{status}: {err}")
            await asyncio.sleep(5)
            waited += 5

        if not video_url:
            raise RuntimeError(
                f"Seedance 任务超时（{max_wait}s，最后状态 {status}），"
                f"可稍后用 task_id={task_id} 查询。"
            )

        local_path = None
        if output_dir:
            dest = Path(output_dir) / f"{filename}.mp4"
            await _download(video_url, dest)
            local_path = str(dest)

        return VideoResult(
            url=video_url,
            local_path=local_path,
            duration=duration,
            provider=self.name,
            task_id=task_id,
            status="completed",
            raw=last_payload,
        )


# --------------------------------------------------------------------------- #
# Cloud track: Kling / Agnes (kept for compatibility)
# --------------------------------------------------------------------------- #

class KlingProvider(BaseVideoProvider):
    name = "kling"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.base_url = base_url or os.getenv("KLING_API_URL", "https://api.klingai.com/v1")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 5.0,
        **kwargs,
    ) -> VideoResult:
        key = self._require_key("KLING_API_KEY")
        payload = {
            "model": kwargs.get("model", "kling-v1-6"),
            "prompt": prompt,
            "duration": duration,
            "width": kwargs.get("width", 1280),
            "height": kwargs.get("height", 720),
        }
        if reference_image:
            payload["image"] = _as_data_url(reference_image)

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/videos/generations",
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        url = data.get("video_url", "")
        local_path = None
        if url and kwargs.get("output_dir"):
            dest = Path(kwargs["output_dir"]) / f"{kwargs.get('filename', 'shot')}.mp4"
            await _download(url, dest)
            local_path = str(dest)

        return VideoResult(
            url=url, local_path=local_path, duration=duration, provider="kling",
            task_id=data.get("task_id") or data.get("id"), raw=data,
        )


class AgnesProvider(BaseVideoProvider):
    """Agnes Video 2.5 (text / keyframe / reference modes).

    Delegates to AgnesVideoClient so the workflow engine gets the richer result
    (local_path, task_id, progress) while still satisfying the shared contract.
    Needs a public asset base URL whenever images are involved.
    """

    name = "agnes"
    needs_public_assets = True

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None, **_):
        super().__init__(api_key, base_url)
        self.model = model

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 5.0,
        *,
        mode: Optional[str] = None,
        size: str = "720P",
        aspect_ratio: str = "16:9",
        seed: Optional[int] = None,
        last_frame: Optional[str] = None,
        images: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        max_wait: float = 900.0,
        on_progress: Optional[Callable[[int, str], None]] = None,
        **kwargs,
    ) -> VideoResult:
        from .agnes import get_agnes_client

        client = get_agnes_client(api_key=self.api_key, base_url=self.base_url, model=self.model)
        result = await client.generate(
            prompt=prompt, mode=mode, seconds=duration, size=size,
            aspect_ratio=aspect_ratio, seed=seed, first_frame=reference_image,
            last_frame=last_frame, images=images, output_dir=output_dir,
            max_wait=max_wait, on_progress=on_progress,
        )
        return VideoResult(
            url=result.url or "",
            local_path=result.local_path,
            duration=float(result.seconds or duration),
            provider="agnes",
            task_id=result.video_id,
            status=result.status,
            raw=result.raw,
        )


class ZhipuVideoProvider(BaseVideoProvider):
    """智谱 cogvideox-flash —— 官方免费视频模型（国内正规通道）。

    POST {base}/api/paas/v4/videos/generations -> {"id": ...}
    轮询 GET {base}/api/paas/v4/async-result/{id} 直到 task_status=SUCCESS，
    取 video_result[].url。图生视频传 reference_image（≤5MB png/jpeg）。
    注意：flash 不支持首尾帧（cogvideox-3 付费支持）；with_audio 可出 AI 音效。
    """

    name = "zhipu"
    needs_public_assets = False

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None, **_):
        super().__init__(api_key, base_url)
        self.base_url = (base_url or os.getenv("ZHIPU_BASE_URL")
                         or "https://open.bigmodel.cn").rstrip("/")
        self.model = model or os.getenv("ZHIPU_VIDEO_MODEL", "cogvideox-flash")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 5.0,
        *,
        with_audio: bool = False,
        quality: str = "speed",
        output_dir: Optional[str] = None,
        filename: str = "shot",
        max_wait: float = 600,
        on_progress: Optional[Callable[[int, str], None]] = None,
        **kwargs,
    ) -> VideoResult:
        key = self._require_key("ZHIPU_API_KEY")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}

        async def _attempt(text: str, image: Optional[str], tag: str) -> str:
            """提交一个任务并轮询到终态，返回视频 URL。"""
            payload: Dict[str, Any] = {
                "model": self.model,
                "prompt": text,
                "quality": quality,
                # 源头去水印（官方参数，默认 true 出「AI生成」角标）
                "watermark_enabled": False,
            }
            if image:
                payload["image_url"] = _shrink_reference_image(image)
            if with_audio:
                payload["with_audio"] = True

            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/paas/v4/videos/generations",
                    json=payload, headers=headers,
                )
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"智谱视频提交失败（HTTP {resp.status_code}）: {resp.text[:400]}"
                    )
                task_id = resp.json().get("id")
            if not task_id:
                raise RuntimeError("智谱视频未返回任务 id")

            deadline = time.time() + max_wait
            status, video_url, last_payload = "", "", {}
            while time.time() < deadline:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    r = await client.get(
                        f"{self.base_url}/api/paas/v4/async-result/{task_id}", headers=headers
                    )
                    r.raise_for_status()
                    last_payload = r.json()
                status = last_payload.get("task_status", "")
                if on_progress:
                    try:
                        elapsed = max_wait - (deadline - time.time())
                        pct = min(int(elapsed / max(max_wait, 1) * 100), 95)
                        on_progress(pct, f"{status}{' · ' + tag if tag else ''}")
                    except Exception:
                        pass
                if status == "SUCCESS":
                    vr = last_payload.get("video_result") or []
                    video_url = (vr[0].get("url") if vr else "") or ""
                    break
                if status in ("FAIL", "FAILED"):
                    # 智谱 FAIL 不返回原因，只留一个 request_id。把 prompt 片段
                    # 打出来，否则用户看到的就是一句没有线索的「任务失败」。
                    raise RuntimeError(
                        f"智谱任务失败（{tag or '首次提交'}，request_id="
                        f"{last_payload.get('request_id', '-')}）："
                        f"prompt {len(text)} 字「{text[:90]}…」"
                        f"{' · 带参考图' if image else ' · 无参考图'}"
                    )
                await asyncio.sleep(5)

            if not video_url:
                raise RuntimeError(
                    f"智谱任务超时（{max_wait}s，最后状态 {status}），task_id={task_id}"
                )
            return video_url

        fell_back = False
        try:
            video_url = await _attempt(prompt, reference_image, "图生视频")
        except RuntimeError as first_error:
            if not reference_image:
                raise
            # 图生视频被拒（参考图尺寸/内容不合规是最常见原因）时退回文生视频，
            # 宁可画面和首帧不完全一致，也不要让整条流水线在这一镜断掉。
            #
            # 但这个退回必须看得见：T2V 出来的是模型默认横屏 1792×1024，和竖屏
            # 分镜图完全对不上，成片只能靠模糊垫底。所以把原因写进进度事件，
            # 并在返回值里标 used_fallback，方便事后排查是哪一镜掉了首帧。
            reason = str(first_error)[:120]
            fell_back = True
            if on_progress:
                on_progress(5, f"⚠️ 图生视频被拒（{reason}），退回文生视频：画面将不跟随分镜图")
            try:
                video_url = await _attempt(prompt, None, "文生视频")
            except RuntimeError:
                raise first_error

        local_path = None
        if output_dir:
            dest = Path(output_dir) / f"{filename}.mp4"
            await _download(video_url, dest)
            local_path = str(dest)
        return VideoResult(url=video_url, local_path=local_path, duration=duration,
                           provider=self.name, task_id=None, status="completed",
                           used_fallback=fell_back)


class ModelScopeVideoProvider(BaseVideoProvider):
    """魔搭 ModelScope API-Inference —— Wan2.2 T2V/I2V 免费额度（体验级）。

    POST {base}/v1/async/videos -> 任务 id；轮询 GET {base}/v1/async-task-v2/{id}。
    免费模型：Wan-AI/Wan2.2-T2V-A14B（文生）、Wan-AI/Wan2.2-I2V-A14B（图生）。
    每日 2000 次共享额度，不适合生产并发；端点字段以魔搭模型页「Use via API」为准。
    """

    name = "modelscope"
    needs_public_assets = False

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None, **_):
        super().__init__(api_key, base_url)
        self.base_url = (base_url or os.getenv("MODELSCOPE_BASE_URL")
                         or "https://api-inference.modelscope.cn").rstrip("/")
        self.model = model or os.getenv("MODELSCOPE_VIDEO_MODEL", "Wan-AI/Wan2.2-T2V-A14B")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 5.0,
        *,
        output_dir: Optional[str] = None,
        filename: str = "shot",
        max_wait: float = 900,
        on_progress: Optional[Callable[[int, str], None]] = None,
        **kwargs,
    ) -> VideoResult:
        key = self._require_key("MODELSCOPE_TOKEN")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        model = self.model
        if reference_image and "I2V" not in model:
            model = "Wan-AI/Wan2.2-I2V-A14B"
        payload: Dict[str, Any] = {"model": model, "prompt": prompt}
        if reference_image:
            payload["image_url"] = _shrink_reference_image(reference_image)

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{self.base_url}/v1/async/videos",
                                     json=payload, headers=headers)
            if resp.status_code >= 400:
                raise RuntimeError(f"魔搭视频提交失败（HTTP {resp.status_code}）: {resp.text[:500]}")
            body = resp.json()
        task_id = body.get("task_id") or body.get("video_id") or body.get("id")
        if not task_id:
            raise RuntimeError(f"魔搭未返回任务 id: {json.dumps(body, ensure_ascii=False)[:300]}")

        deadline = time.time() + max_wait
        status, video_url, last_payload = "", "", {}
        while time.time() < deadline:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(f"{self.base_url}/v1/async-task-v2/{task_id}",
                                     headers=headers)
                r.raise_for_status()
                last_payload = r.json()
            status = str(last_payload.get("status") or last_payload.get("task_status") or "")
            if status.lower() in ("succeeded", "success"):
                from .image_providers import _extract_urls
                urls = _extract_urls(last_payload)
                video_url = urls[0] if urls else ""
                break
            if status.lower() in ("failed", "error"):
                raise RuntimeError(f"魔搭任务失败: {json.dumps(last_payload, ensure_ascii=False)[:400]}")
            if on_progress:
                try:
                    on_progress(min(int((deadline - time.time()) / max(max_wait, 1) * 95), 95), status)
                except Exception:
                    pass
            await asyncio.sleep(5)

        if not video_url:
            raise RuntimeError(f"魔搭任务超时（{max_wait}s，最后状态 {status}），task_id={task_id}")

        local_path = None
        if output_dir:
            dest = Path(output_dir) / f"{filename}.mp4"
            await _download(video_url, dest)
            local_path = str(dest)
        return VideoResult(url=video_url, local_path=local_path, duration=duration,
                           provider=self.name, task_id=task_id, status="completed", raw=last_payload)


# --------------------------------------------------------------------------- #
# 阿里百炼 DashScope（万相 / happyhorse）：含整段视频改风格通道
# --------------------------------------------------------------------------- #

_DS_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}
_DS_FAILED = {"FAILED", "CANCELED", "UNKNOWN"}
# 用户常把完整端点甚至兼容模式地址填进 DASHSCOPE_BASE_URL，这里统一剥回 host
_DS_SUFFIXES = ("/compatible-mode/v1", "/api/v1")


def _is_video_edit_model(model: str) -> bool:
    low = (model or "").lower()
    return "videoedit" in low or "video-edit" in low


def _ds_base_url(base_url: Optional[str]) -> str:
    base = ((base_url or os.getenv("DASHSCOPE_BASE_URL")
             or "https://dashscope.aliyuncs.com")).strip().rstrip("/")
    for suffix in _DS_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base + "/api/v1"


class DashscopeVideoProvider(BaseVideoProvider):
    """阿里百炼视频（原生异步端点）。魔改线用它做「整段换世界观」的 wan2.7-videoedit。

    提交 ``POST {base}/services/aigc/video-generation/video-synthesis``，body：
    ``{"model": …, "input": {"prompt": …, "media": […]}, "parameters": {…}}``
    → ``output.task_id``；再 ``GET {base}/tasks/{id}`` 轮到 ``output.task_status``
    终态，取 ``output.video_url`` 下载。任务 id 与 URL 都只在提交它的域名上有效。

    三种 mode：
    * ``text`` / ``first_frame`` / ``first_last_frame``：media 放 first_frame、last_frame，
      本地图片转 data URI（百炼接受 data URI，所以不需要公网隧道）。
    * ``video_edit``（模型名含 videoedit）：media 放
      ``{"type": "video", "url": 原片}`` + 可选 ``{"type": "reference_image", "url": 风格图}``。
      视频不能塞 base64（整段几 MB 起，请求体会被拒），必须是 http(s) URL ——
      本地文件要先经 PUBLIC_ASSET_BASE_URL 发布，否则这里直接报错说清缺什么。
    """

    name = "dashscope"
    needs_public_assets = False  # 图片走 data URI；只有视频才需要公网 URL，单独校验

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None, **_):
        super().__init__(api_key, base_url)
        self.base_url = _ds_base_url(base_url)
        self.model = model or os.getenv("DASHSCOPE_VIDEO_MODEL") or "happyhorse-1.1-i2v"

    def _media_for(self, mode: str, reference_image: Optional[str], last_frame: Optional[str],
                   source_video: Optional[str]) -> List[Dict[str, str]]:
        if mode == "video_edit" or _is_video_edit_model(self.model):
            if not source_video:
                raise ValueError(
                    f"{self.name} 的 {self.model} 是视频编辑模型，必须给它一段源视频："
                    "节点写 \"source_video_from\": \"input_clip\"（上游 video_input 节点），"
                    "或直接写 \"source_video\": \"公网 URL\"。"
                )
            if not str(source_video).startswith(("http://", "https://")):
                raise ValueError(
                    f"{self.name} 只能读公网视频 URL，拿到的是本地路径 {source_video}。"
                    "在 .env 配 PUBLIC_ASSET_BASE_URL 并把后端暴露到公网（ngrok http 8000 之类），"
                    "引擎会自动把源视频发布成 URL；不想开隧道就改用 provider: comfyui 的 VACE 预设。"
                )
            media: List[Dict[str, str]] = [{"type": "video", "url": str(source_video)}]
            if reference_image:
                media.append({"type": "reference_image",
                              "url": _as_data_url(reference_image)})
            return media
        media = []
        if mode in ("first_frame", "first_last_frame") and reference_image:
            media.append({"type": "first_frame", "url": _as_data_url(reference_image)})
        if mode == "first_last_frame" and last_frame:
            media.append({"type": "last_frame", "url": _as_data_url(last_frame)})
        return media

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 5.0,
        *,
        mode: str = "first_frame",
        last_frame: Optional[str] = None,
        source_video: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        resolution: str = "720p",
        ratio: Optional[str] = None,
        seed: Optional[int] = None,
        watermark: bool = False,
        output_dir: Optional[str] = None,
        filename: str = "shot",
        max_wait: float = 900,
        on_progress: Optional[Callable[[int, str], None]] = None,
        **kwargs,
    ) -> VideoResult:
        key = self._require_key("DASHSCOPE_API_KEY")
        media = self._media_for(mode, reference_image, last_frame, source_video)

        input_block: Dict[str, Any] = {"prompt": prompt}
        if media:
            input_block["media"] = media
        if negative_prompt:
            input_block["negative_prompt"] = negative_prompt

        parameters: Dict[str, Any] = {
            "resolution": (resolution or "720p").upper(),
            "duration": int(round(duration)),
            "watermark": bool(watermark),
        }
        # 带首帧/源视频时宽高比由画面自己决定，上游会忽略甚至拒绝 ratio，所以只在纯文生时下传。
        if not media and ratio:
            parameters["ratio"] = ratio
        if seed is not None:
            parameters["seed"] = seed

        payload = {"model": self.model, "input": input_block, "parameters": parameters}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                   "X-DashScope-Async": "enable"}

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{self.base_url}/services/aigc/video-generation/video-synthesis",
                                     json=payload, headers=headers)
            body = {} if resp.status_code >= 400 or "json" not in resp.headers.get(
                "content-type", "") else resp.json()
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"百炼视频提交失败（HTTP {resp.status_code}）: {resp.text[:500]}"
                )
            task_id = (body.get("output") or {}).get("task_id")
            if not task_id:
                # 200 也可能带顶层 code/message（如 InvalidApiKey），没有 task_id 就没法轮询
                raise RuntimeError(
                    f"百炼未返回 task_id: {json.dumps(body, ensure_ascii=False)[:400]}"
                )

        deadline = time.time() + max_wait
        status, video_url, final = "", "", {}
        while time.time() < deadline:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(f"{self.base_url}/tasks/{task_id}", headers=headers)
                r.raise_for_status()
                final = r.json()
            status = str((final.get("output") or {}).get("task_status") or "")
            if status in _DS_FAILED:
                out = final.get("output") or {}
                raise RuntimeError(
                    f"百炼任务失败 status={status} code={out.get('code') or 'unknown'}: "
                    f"{out.get('message') or ''}（task_id={task_id}）"
                )
            if status == "SUCCEEDED":
                video_url = (final.get("output") or {}).get("video_url") or ""
                break
            if on_progress:
                try:
                    on_progress(min(int((1 - (deadline - time.time()) / max(max_wait, 1)) * 95), 95),
                                status)
                except Exception:
                    pass
            await asyncio.sleep(15)  # 官方建议轮询间隔 15s

        if not video_url:
            raise RuntimeError(
                f"百炼任务超时（{max_wait}s，最后状态 {status or '未开始'}），"
                f"task_id={task_id} 24 小时内仍可用同一地址查询。"
            )

        local_path = None
        if output_dir:
            dest = Path(output_dir) / f"{filename}.mp4"
            await _download(video_url, dest)
            local_path = str(dest)
        return VideoResult(url=video_url, local_path=local_path, duration=duration,
                           provider=self.name, task_id=task_id, status="completed", raw=final)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

PROVIDERS: Dict[str, Any] = {
    "comfyui": ComfyUIVideoProvider,
    "seedance": SeedanceProvider,
    "kling": KlingProvider,
    "agnes": AgnesProvider,
    "zhipu": ZhipuVideoProvider,
    "modelscope": ModelScopeVideoProvider,
    "dashscope": DashscopeVideoProvider,
}

DEFAULT_PROVIDER = "agnes"  # 本机跑不动 ComfyUI；Agnes 2.5 Flash 限时免费，开箱即用


def get_provider(name: Optional[str] = None, api_key: Optional[str] = None, **kwargs):
    """Instantiate a provider by name.

    Only real providers are registered — nothing here raises NotImplementedError,
    so a misconfigured node fails loudly at selection time instead of mid-run.
    """
    key = (name or os.getenv("VIDEO_PROVIDER") or DEFAULT_PROVIDER).lower()
    factory = PROVIDERS.get(key)
    if factory is None:
        raise ValueError(f"Unknown video provider {key!r}. Available: {sorted(PROVIDERS)}")
    return factory(api_key=api_key, **kwargs)


def list_providers() -> List[Dict[str, Any]]:
    return [
        {"id": "comfyui", "label": "ComfyUI Wan 2.2 I2V", "local": True,
         "default": True, "needs_public_assets": False,
         "modes": ["first_frame", "text"], "note": "需要本机/远程 ComfyUI"},
        {"id": "seedance", "label": "Seedance（火山方舟）", "local": False,
         "default": False, "needs_public_assets": False,
         "modes": ["text", "first_frame", "first_last_frame", "reference"],
         "note": "参考图走 base64，无需公网 URL；需 ARK_API_KEY"},
        {"id": "kling", "label": "Kling", "local": False, "default": False,
         "needs_public_assets": False, "modes": ["text", "image"],
         "note": "需 KLING_API_KEY"},
        {"id": "agnes", "label": "Agnes Video 2.5（2.5 Flash 限时免费）", "local": False,
         "default": False, "needs_public_assets": True,
         "modes": ["text", "keyframe", "reference"],
         "note": "agnes-video-2.5-flash 当前 $0/秒（720P，4-12s）；涉及图片时需 PUBLIC_ASSET_BASE_URL",
         "free": "promo"},
        {"id": "zhipu", "label": "智谱 cogvideox-flash（官方免费）", "local": False,
         "needs_public_assets": False, "modes": ["text", "image"],
         "note": "官方免费模型；图生视频传参考图；flash 不支持首尾帧；需 ZHIPU_API_KEY",
         "free": True},
        {"id": "modelscope", "label": "魔搭 Wan2.2（免费额度）", "local": False,
         "needs_public_assets": False, "modes": ["text", "image"],
         "note": "每日 2000 次共享额度，体验级；需 MODELSCOPE_TOKEN",
         "free": True},
        {"id": "dashscope", "label": "阿里百炼 万相 / happyhorse", "local": False,
         "needs_public_assets": False,
         "modes": ["text", "first_frame", "first_last_frame", "video_edit"],
         "note": "图片走 data URI 不用开隧道；video_edit（model 含 videoedit，如 "
                 "wan2.7-videoedit）要整段源视频的公网 URL，需 PUBLIC_ASSET_BASE_URL；"
                 "需 DASHSCOPE_API_KEY"},
    ]
