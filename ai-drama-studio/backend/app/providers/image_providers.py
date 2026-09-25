"""Unified image generation providers.

Two tracks, chosen per node at runtime:

* ``comfyui`` — local/remote ComfyUI. Full control: LoRA, IP-Adapter, ControlNet,
  reference images, per-node seed. This is the track the 5060 Ti box should use.
* ``http``    — any JSON image API (Seedream, Doubao, a self-hosted gateway...).
  Used when there is no GPU attached, e.g. this laptop.

Both return the same :class:`ImageResult`, so the workflow engine does not care
which one ran.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

WORKFLOW_DIR = Path(__file__).parent.parent.parent / "config" / "comfyui_workflows"


@dataclass
class ImageResult:
    """Normalised image result. ``paths`` are local files, ``urls`` are remote."""

    paths: List[str] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)
    provider: str = ""
    workflow: Optional[str] = None
    prompt_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def first(self) -> Optional[str]:
        return self.paths[0] if self.paths else (self.urls[0] if self.urls else None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "paths": self.paths,
            "urls": self.urls,
            "provider": self.provider,
            "workflow": self.workflow,
            "prompt_id": self.prompt_id,
        }


class BaseImageProvider(ABC):
    name = "base"
    label = "Base"

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        reference_images: Optional[List[str]] = None,
        loras: Optional[List[Dict[str, Any]]] = None,
        output_dir: Optional[str] = None,
        filename: str = "image",
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        **kwargs,
    ) -> ImageResult:
        ...


class ComfyUIImageProvider(BaseImageProvider):
    """Render images through a ComfyUI workflow template.

    The template declares how logical params map onto node inputs via a ``params``
    block, e.g.::

        "params": {"prompt": {"node": "93", "field": "text"},
                   "seed":   {"node": "100", "field": "seed"}}

    Any ``{{placeholder}}`` left in the graph is rendered from the same context, so
    templates work even without a ``params`` block.
    """

    name = "comfyui"
    label = "ComfyUI（本地/远程）"

    def __init__(
        self,
        workflow_file: str = "scene-generation.json",
        server_url: Optional[str] = None,
        output_node: Optional[str] = None,
    ):
        self.workflow_file = workflow_file
        self.server_url = server_url
        self.output_node = output_node

    def _resolve_paths(self) -> tuple[Path, Dict[str, Any], Dict[str, Any]]:
        path = Path(self.workflow_file)
        if not path.is_absolute():
            path = WORKFLOW_DIR / path
        if not path.exists():
            raise FileNotFoundError(
                f"找不到 ComfyUI 工作流模板：{path}。可用模板："
                f"{[p.name for p in WORKFLOW_DIR.glob('*.json')]}"
            )
        from .comfyui import ComfyUIClient

        meta = ComfyUIClient.load_workflow_meta(path)
        graph = ComfyUIClient.load_workflow(path)
        return path, graph, meta

    def _build_patches(
        self,
        meta: Dict[str, Any],
        *,
        prompt: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        seed: Optional[int],
        steps: Optional[int],
        cfg: Optional[float],
        uploaded: Optional[Dict[str, str]],
    ) -> Dict[str, Dict[str, Any]]:
        """Turn logical params into ``{node_id: {field: value}}`` patches."""
        params: Dict[str, Dict[str, str]] = meta.get("params") or {}
        values: Dict[str, Any] = {
            "prompt": prompt,
            "positive_prompt": prompt,
            "negative_prompt": negative_prompt or "",
            "width": width,
            "height": height,
            "steps": steps,
            "cfg": cfg,
        }
        if seed is not None:
            values["seed"] = seed
        if uploaded:
            values.update(uploaded)

        patches: Dict[str, Dict[str, Any]] = {}
        for key, value in values.items():
            if value is None:
                continue
            mapping = params.get(key)
            if not mapping:
                continue
            node = str(mapping.get("node"))
            field = mapping.get("field")
            if not field:
                continue
            patches.setdefault(node, {})[field] = value
        return patches

    async def generate(
        self,
        prompt: str,
        *,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        reference_images: Optional[List[str]] = None,
        loras: Optional[List[Dict[str, Any]]] = None,
        output_dir: Optional[str] = None,
        filename: str = "image",
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        timeout: float = 900,
        **kwargs,
    ) -> ImageResult:
        from .comfyui import ComfyUIClient, ComfyUIError

        path, graph, meta = self._resolve_paths()
        client = ComfyUIClient(server_url=self.server_url, capability="image")

        if not client.is_available():
            raise ComfyUIError(
                f"ComfyUI 不可达（{client.server_url}）。"
                "本机无 GPU 时可改用云端 image provider（节点配置 provider: http），"
                "或把 COMFYUI_SERVER_URL 指向已装好 ComfyUI 的机器。"
            )

        uploaded: Dict[str, str] = {}
        for idx, ref in enumerate(reference_images or []):
            ref_path = Path(ref)
            if not ref_path.exists():
                raise FileNotFoundError(f"参考图不存在：{ref_path}")
            name = client.upload_image(ref_path, f"ref_{uuid.uuid4().hex[:8]}{ref_path.suffix}")
            uploaded[f"reference_image" if idx == 0 else f"reference_image_{idx}"] = name

        patches = self._build_patches(
            meta, prompt=prompt, negative_prompt=negative_prompt, width=width,
            height=height, seed=seed, steps=steps, cfg=cfg, uploaded=uploaded,
        )

        context = {
            "prompt": prompt,
            "positive_prompt": prompt,
            "negative_prompt": negative_prompt or "",
            "width": width,
            "height": height,
            "seed": seed if seed is not None else ComfyUIClient.random_seed(),
        }
        graph = _render_placeholders(graph, context)
        if patches:
            graph = ComfyUIClient.patch_workflow(graph, patches, strict=False)

        out_dir = Path(output_dir or "output/images")
        out_dir.mkdir(parents=True, exist_ok=True)

        def _run():
            return client.generate(
                graph,
                str(self.output_node or meta.get("output_node") or "9"),
                out_dir / f"{filename}.png",
                timeout=timeout,
                on_progress=on_progress,
                preflight=True,
            )

        paths = await asyncio.to_thread(_run)
        return ImageResult(
            paths=[str(p) for p in paths],
            provider=self.name,
            workflow=path.stem,
            raw={"server": client.server_url},
        )


class HttpImageProvider(BaseImageProvider):
    """Generic JSON image API — for machines without a usable GPU.

    Env: ``IMAGE_API_URL``, ``IMAGE_API_KEY``, ``IMAGE_API_MODEL``.
    The response is read tolerantly from ``data[0].url`` / ``data[0].b64_json`` /
    ``images[0]`` / ``url``.
    """

    name = "http"
    label = "云端 HTTP 图像 API"

    def __init__(self, api_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None):
        self.api_url = api_url or os.getenv("IMAGE_API_URL", "")
        self.api_key = api_key or os.getenv("IMAGE_API_KEY", "")
        self.model = model or os.getenv("IMAGE_API_MODEL", "")

    async def generate(
        self,
        prompt: str,
        *,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        reference_images: Optional[List[str]] = None,
        loras: Optional[List[Dict[str, Any]]] = None,
        output_dir: Optional[str] = None,
        filename: str = "image",
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        **kwargs,
    ) -> ImageResult:
        if not self.api_url:
            raise RuntimeError(
                "云端图像 provider 未配置：设置 IMAGE_API_URL / IMAGE_API_KEY，"
                "或改用 provider: comfyui。"
            )

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": f"{width}x{height}",
            "n": 1,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = seed
        if reference_images:
            payload["image"] = _as_data_url(reference_images[0])

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(self.api_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        urls = _extract_urls(data)
        b64s = _extract_b64(data)
        paths: List[str] = []
        if b64s or output_dir:
            out_dir = Path(output_dir or "output/images")
            out_dir.mkdir(parents=True, exist_ok=True)
            for i, b64 in enumerate(b64s):
                target = out_dir / (f"{filename}.png" if len(b64s) == 1 else f"{filename}_{i:03d}.png")
                target.write_bytes(base64.b64decode(b64))
                paths.append(str(target))

        if not urls and not paths:
            raise RuntimeError(f"云端图像 API 未返回可用结果：{json.dumps(data, ensure_ascii=False)[:400]}")
        return ImageResult(paths=paths, urls=urls, provider=self.name, raw=data)


class PollinationsImageProvider(BaseImageProvider):
    """完全免费的生图通道（无需任何 Key）。

    GET https://image.pollinations.ai/prompt/<urlencoded prompt>?width=&height=&seed=&nologo=true
    直接返回 PNG 字节。适合低成本跑通流水线与抽卡预览；生产级一致性请用
    comfyui / siliconflow / modelscope。
    """

    name = "pollinations"
    label = "Pollinations 免费生图"

    def __init__(self, api_url: Optional[str] = None, api_key: Optional[str] = None, **_):
        self.api_url = api_url or os.getenv(
            "POLLINATIONS_API_URL", "https://image.pollinations.ai/prompt"
        )
        # 2026 起官方要求 Key（enter.pollinations.ai 注册，sk_*/pk_*）
        self.api_key = api_key or os.getenv("POLLINATIONS_KEY", "")

    async def generate(
        self,
        prompt: str,
        *,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        reference_images: Optional[List[str]] = None,
        loras: Optional[List[Dict[str, Any]]] = None,
        output_dir: Optional[str] = None,
        filename: str = "image",
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        **kwargs,
    ) -> ImageResult:
        import urllib.parse

        full_prompt = prompt
        if negative_prompt:
            full_prompt += f" | avoid: {negative_prompt}"
        params = {"width": width, "height": height, "nologo": "true", "safe": "false"}
        if seed is not None:
            params["seed"] = seed
        url = (
            f"{self.api_url.rstrip('/')}/{urllib.parse.quote(full_prompt)}"
            + "?" + urllib.parse.urlencode(params)
        )
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "image" not in content_type:
                raise RuntimeError(
                    f"Pollinations 未返回图片（content-type={content_type}）：{resp.text[:200]}"
                )
            data = resp.content

        out_dir = Path(output_dir or "output/images")
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{filename}.png"
        target.write_bytes(data)
        public_url = f"{self.api_url.rstrip('/')}/{urllib.parse.quote(full_prompt)}?{urllib.parse.urlencode(params)}"
        return ImageResult(paths=[str(target)], urls=[public_url], provider=self.name, raw={"bytes": len(data)})


class _OpenAIStyleImageProvider(BaseImageProvider):
    """OpenAI 兼容 ``/v1/images/generations`` 生图基类（智谱/魔搭/硅基流动共用）。

    差异点由子类声明：端点、默认模型、尺寸字段名、鉴权环境变量。
    返回的 URL 一律立即下载落盘（硅基流动 1 小时过期，魔搭也会回收）。
    """

    name = "openai-style"
    label = "OpenAI 兼容生图"
    api_url = ""
    default_model = ""
    env_var = ""
    size_field = "size"  # siliconflow 用 image_size
    size_is_pixels = True

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, **_):
        self.api_key = api_key or os.getenv(self.env_var, "")
        self.model = model or os.getenv(f"{self.env_var}_MODEL", "") or self.default_model

    async def generate(
        self,
        prompt: str,
        *,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        reference_images: Optional[List[str]] = None,
        loras: Optional[List[Dict[str, Any]]] = None,
        output_dir: Optional[str] = None,
        filename: str = "image",
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        **kwargs,
    ) -> ImageResult:
        if not self.api_key:
            raise RuntimeError(
                f"{self.label} 未配置：设置 {self.env_var}（设置页「免费通道」里填）"
            )
        size = f"{width}x{height}" if self.size_is_pixels else f"{width}*{height}"
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            self.size_field: size,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = seed

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                self.api_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"{self.label} 提交失败（HTTP {resp.status_code}）: {resp.text[:400]}"
                )
            data = resp.json()
            urls = _extract_urls(data)
            if not urls:
                raise RuntimeError(
                    f"{self.label} 未返回图片 URL: {json.dumps(data, ensure_ascii=False)[:400]}"
                )
            # URL 会过期，立即下载
            out_dir = Path(output_dir or "output/images")
            out_dir.mkdir(parents=True, exist_ok=True)
            paths: List[str] = []
            for i, u in enumerate(urls):
                dest = out_dir / (f"{filename}.png" if len(urls) == 1 else f"{filename}_{i:03d}.png")
                r = await client.get(u)
                r.raise_for_status()
                dest.write_bytes(r.content)
                paths.append(str(dest))

        return ImageResult(paths=paths, urls=urls, provider=self.name, raw=data)


class ZhipuImageProvider(_OpenAIStyleImageProvider):
    """智谱 cogview-3-flash —— 官方免费生图模型。

    实测 2026-09：cogview-4-flash 已下线（1211 模型不存在），免费档为
    cogview-3-flash；cogview-4 / cogview-4-250304 可用但计费。
    """

    name = "zhipu"
    label = "智谱 cogview-3-flash（免费）"
    api_url = "https://open.bigmodel.cn/api/paas/v4/images/generations"
    default_model = "cogview-3-flash"
    env_var = "ZHIPU_API_KEY"


class SiliconFlowImageProvider(_OpenAIStyleImageProvider):
    """硅基流动 Kolors —— 免费层生图，中文理解强。URL 1 小时过期，已自动落盘。"""

    name = "siliconflow"
    label = "硅基流动 Kolors（免费层）"
    api_url = "https://api.siliconflow.cn/v1/images/generations"
    default_model = "Kwai-Kolors/Kolors"
    env_var = "SILICONFLOW_API_KEY"
    size_field = "image_size"


class ModelScopeImageProvider(_OpenAIStyleImageProvider):
    """魔搭 ModelScope API-Inference —— 每日 2000 次免费共享额度。

    免费生图模型：Tongyi-MAI/Z-Image-Turbo、Qwen/Qwen-Image-2512、FLUX.1-schnell。
    Token: https://modelscope.cn/my/myaccesstoken
    """

    name = "modelscope"
    label = "魔搭 API-Inference（每日 2000 次免费）"
    api_url = "https://api-inference.modelscope.cn/v1/images/generations"
    default_model = "Tongyi-MAI/Z-Image-Turbo"
    env_var = "MODELSCOPE_TOKEN"


PROVIDERS: Dict[str, Any] = {
    "comfyui": ComfyUIImageProvider,
    "http": HttpImageProvider,
    "pollinations": PollinationsImageProvider,
    "zhipu": ZhipuImageProvider,
    "siliconflow": SiliconFlowImageProvider,
    "modelscope": ModelScopeImageProvider,
}

DEFAULT_PROVIDER = "comfyui"


def get_image_provider(name: Optional[str] = None, **kwargs) -> BaseImageProvider:
    key = (name or os.getenv("IMAGE_PROVIDER") or DEFAULT_PROVIDER).lower()
    factory = PROVIDERS.get(key)
    if factory is None:
        raise ValueError(f"Unknown image provider {key!r}. Available: {sorted(PROVIDERS)}")
    return factory(**kwargs)


def list_image_providers() -> List[Dict[str, Any]]:
    return [
        {"id": "comfyui", "label": ComfyUIImageProvider.label, "local": True,
         "supports_reference": True, "note": "需要 ComfyUI 服务；5060 Ti 16G 可跑 SDXL/Flux"},
        {"id": "http", "label": HttpImageProvider.label, "local": False,
         "supports_reference": True, "note": "需配置 IMAGE_API_URL / IMAGE_API_KEY"},
        {"id": "pollinations", "label": PollinationsImageProvider.label, "local": False,
         "supports_reference": False, "note": "2026 起需 API Key（POLLINATIONS_KEY）；免费额度为 pollen 预算制",
         "free": "key-required"},
        {"id": "zhipu", "label": ZhipuImageProvider.label, "local": False,
         "supports_reference": False, "note": "官方免费模型 cogview-4-flash；需 ZHIPU_API_KEY",
         "free": True},
        {"id": "siliconflow", "label": SiliconFlowImageProvider.label, "local": False,
         "supports_reference": False, "note": "免费层 Kolors；URL 1 小时过期已自动下载",
         "free": True},
        {"id": "modelscope", "label": ModelScopeImageProvider.label, "local": False,
         "supports_reference": False, "note": "每日 2000 次共享额度；Z-Image-Turbo/Qwen-Image/FLUX-schnell",
         "free": True},
    ]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _as_data_url(path_str: str) -> str:
    path = Path(path_str)
    if not path.exists():
        return path_str  # already a URL
    mime = "image/png"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif path.suffix.lower() == ".webp":
        mime = "image/webp"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def _extract_urls(data: Any) -> List[str]:
    found: List[str] = []

    def walk(node: Any):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("url", "image_url", "image") and isinstance(value, str) \
                        and value.startswith(("http://", "https://")):
                    found.append(value)
                elif key == "image_url" and isinstance(value, dict) and isinstance(value.get("url"), str):
                    found.append(value["url"])
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def _extract_b64(data: Any) -> List[str]:
    found: List[str] = []

    def walk(node: Any):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("b64_json", "b64", "base64") and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def _render_placeholders(graph: dict, context: Dict[str, Any]) -> dict:
    """Replace ``{{key}}`` in every string input.

    ComfyUI rejects string seeds, so numeric-looking values are coerced by
    :meth:`ComfyUIClient.patch_workflow` if a params block is present; here we
    also cast pure-integer placeholders directly.
    """
    import copy

    out = copy.deepcopy(graph)
    for _nid, node in (out or {}).items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, value in list(inputs.items()):
            if not isinstance(value, str) or "{{" not in value:
                continue
            rendered = value
            for ckey, cval in context.items():
                if cval is None:
                    continue
                rendered = rendered.replace("{{" + ckey + "}}", str(cval))
            inputs[key] = rendered
    return out
