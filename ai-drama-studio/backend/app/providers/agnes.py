"""Agnes Video 2.5 provider.

Implements the official OpenAI-Videos-compatible API. Verified against the live
endpoint on 2026-09-01 (see docs/AGNES_VIDEO_2.5.md for the probe log).

    Base URL : https://apihub.agnes-ai.com/v1
    Create   : POST  /v1/videos
    Poll     : GET   {api_host}/agnesapi?video_id=<VIDEO_ID>&model_name=<MODEL>
    Result   : metadata.url   (a top-level `url` is also accepted as a fallback)

Differences from the previous implementation, which did NOT work:
  * it talked to a non-existent local proxy (127.0.0.1:57324) instead of apihub
  * it sent `resolution` / `duration` / `mode="T2V"`, none of which the API accepts
  * it polled `GET /v1/videos/{id}`, which is not a documented endpoint
  * it read the result from `output.url` instead of `metadata.url`
  * it had no support for first/last frame or reference images
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from dotenv import load_dotenv

# Project root .env (ai-drama-studio/.env) — resolved from this file so it works
# regardless of the process working directory.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
# flash 档当前限时 $0/秒，作为默认；质量优先时在设置页切 agnes-video-2.5
DEFAULT_MODEL = os.getenv("AGNES_VIDEO_MODEL", "agnes-video-2.5-flash")

# The legacy local proxy. Kept only so an existing deployment can opt back in by
# setting AGNES_BASE_URL explicitly; it is never the default.
LEGACY_PROXY_URL = "http://127.0.0.1:57324"

MODELS = ("agnes-video-2.5", "agnes-video-2.5-flash", "agnes-video-v2.0")

# Sending anything else returns HTTP 400.
SIZES = ("720P", "960P", "2K")
ASPECT_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

# `seconds` is a *string* between "4" and "12".
MIN_SECONDS = 4
MAX_SECONDS = 12

# Terminal / non-terminal statuses returned by /agnesapi.
STATUS_DONE = ("completed",)
STATUS_FAILED = ("failed",)
STATUS_PENDING = ("queued", "in_progress", "pending", "processing")

# 720P output pixels, useful for sizing upstream ComfyUI keyframes.
ASPECT_PIXELS_720P = {
    "21:9": (1680, 720),
    "16:9": (1280, 720),
    "4:3": (960, 720),
    "1:1": (720, 720),
    "3:4": (720, 960),
    "9:16": (720, 1280),
}


class AgnesVideoError(Exception):
    """Raised when the Agnes API rejects a request or a task fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        request_id: Optional[str] = None,
        video_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.video_id = video_id


def _derive_api_host(base_url: str) -> str:
    """The status endpoint lives on the host root, not under /v1."""
    stripped = base_url.rstrip("/")
    return stripped[: -len("/v1")] if stripped.endswith("/v1") else stripped


@dataclass
class AgnesVideoResult:
    """Outcome of a completed generation task."""

    video_id: str
    task_id: str
    status: str
    url: Optional[str] = None
    local_path: Optional[str] = None
    model: Optional[str] = None
    seconds: Optional[str] = None
    size: Optional[str] = None
    aspect_ratio: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "task_id": self.task_id,
            "status": self.status,
            "url": self.url,
            "local_path": self.local_path,
            "model": self.model,
            "seconds": self.seconds,
            "size": self.size,
            "aspect_ratio": self.aspect_ratio,
            "provider": "agnes",
        }


class AgnesVideoClient:
    """Async client for Agnes Video 2.5."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key or os.getenv("AGNES_API_KEY", "")
        self.base_url = (base_url or os.getenv("AGNES_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_host = _derive_api_host(self.base_url)
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    # -- lifecycle ---------------------------------------------------------- #

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            if not self.api_key:
                raise AgnesVideoError(
                    "AGNES_API_KEY is not set. Add it to ai-drama-studio/.env "
                    "or pass api_key explicitly."
                )
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # -- request building --------------------------------------------------- #

    @staticmethod
    def _normalise_seconds(seconds: Any) -> str:
        value = int(round(float(seconds)))
        if not MIN_SECONDS <= value <= MAX_SECONDS:
            raise AgnesVideoError(
                f"seconds must be between {MIN_SECONDS} and {MAX_SECONDS}, got {seconds!r}"
            )
        return str(value)

    @staticmethod
    def _validate_media(mode: str, payload: Dict[str, Any]):
        """Fail fast on mode/media conflicts that the API answers with HTTP 400."""
        has_frame = bool(payload.get("first_frame") or payload.get("last_frame"))
        has_ref = bool(
            payload.get("images") or payload.get("audios") or payload.get("videos")
        )
        if mode == "text" and (has_frame or has_ref):
            raise AgnesVideoError(
                "mode='text' accepts no media fields; drop first_frame/last_frame/"
                "images/audios/videos or switch mode to 'keyframe'/'reference'."
            )
        if mode == "keyframe":
            if not has_frame:
                raise AgnesVideoError(
                    "mode='keyframe' requires first_frame, last_frame, or both."
                )
            if has_ref:
                raise AgnesVideoError(
                    "mode='keyframe' does not accept images/audios/videos."
                )
        if mode == "reference" and not has_ref:
            raise AgnesVideoError(
                "mode='reference' requires at least one of images/audios/videos."
            )

    def build_payload(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        mode: Optional[str] = None,
        seconds: Any = 5,
        size: str = "720P",
        aspect_ratio: str = "16:9",
        seed: Optional[int] = None,
        first_frame: Optional[str] = None,
        last_frame: Optional[str] = None,
        images: Optional[List[str]] = None,
        audios: Optional[List[str]] = None,
        videos: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Assemble a request body that matches the documented contract."""
        if size not in SIZES:
            raise AgnesVideoError(
                f"size must be one of {SIZES}, got {size!r}. "
                "Pixel dimensions such as '1280x720' are rejected."
            )
        if aspect_ratio not in ASPECT_RATIOS:
            raise AgnesVideoError(
                f"aspect_ratio must be one of {ASPECT_RATIOS}, got {aspect_ratio!r}. "
                "'auto' and WIDTHxHEIGHT are not supported."
            )

        # Infer the mode from whatever media was supplied when not told explicitly.
        if mode is None:
            if first_frame or last_frame:
                mode = "keyframe"
            elif images or audios or videos:
                mode = "reference"
            else:
                mode = "text"

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "mode": mode,
            "seconds": self._normalise_seconds(seconds),
            "size": size,
            "aspect_ratio": aspect_ratio,
            "n": 1,
        }
        if seed is not None:
            payload["seed"] = int(seed)

        if first_frame:
            payload["first_frame"] = first_frame
        if last_frame:
            payload["last_frame"] = last_frame
        if images:
            payload["images"] = list(images)
        if audios:
            payload["audios"] = list(audios)
        if videos:
            payload["videos"] = list(videos)

        self._validate_media(mode, payload)
        return payload

    # -- API calls ---------------------------------------------------------- #

    async def create_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST /v1/videos -> raw create response (status `queued`)."""
        client = await self._get_client()
        resp = await client.post(f"{self.base_url}/videos", json=payload)
        body = self._json_or_empty(resp)
        if resp.status_code >= 400:
            raise AgnesVideoError(
                self._error_message(resp.status_code, body),
                status_code=resp.status_code,
                request_id=self._request_id(resp, body),
            )

        video_id = body.get("video_id") or body.get("id") or body.get("task_id")
        if not video_id:
            raise AgnesVideoError(f"Create response contained no video_id: {body}")
        return {**body, "video_id": video_id}

    async def get_task(
        self, video_id: str, model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """GET /agnesapi?video_id=...&model_name=... -> raw status response."""
        client = await self._get_client()
        params = {"video_id": video_id}
        # model_name is mandatory for keyframe/reference; harmless for text.
        params["model_name"] = model_name or self.model
        resp = await client.get(f"{self.api_host}/agnesapi", params=params)
        body = self._json_or_empty(resp)
        if resp.status_code >= 400:
            raise AgnesVideoError(
                self._error_message(resp.status_code, body),
                status_code=resp.status_code,
                request_id=self._request_id(resp, body),
                video_id=video_id,
            )
        return body

    async def wait_for_completion(
        self,
        video_id: str,
        *,
        model_name: Optional[str] = None,
        max_wait: float = 900.0,
        poll_interval: float = 2.0,
        max_poll_interval: float = 10.0,
        on_progress: Optional[Callable[[int, str], None]] = None,
    ) -> AgnesVideoResult:
        """Poll until the task reaches a terminal state.

        Uses exponential backoff so a 429 or a transient 5xx slows polling down
        instead of hammering the API.
        """
        deadline = time.monotonic() + max_wait
        interval = poll_interval
        last_progress = -1
        # Consecutive failures before we give up, so a long outage does not
        # burn the whole budget.
        consecutive_errors = 0

        while time.monotonic() < deadline:
            try:
                body = await self.get_task(video_id, model_name=model_name)
                consecutive_errors = 0
            except AgnesVideoError as exc:
                if exc.status_code == 429 or (exc.status_code or 0) >= 500:
                    consecutive_errors += 1
                    if consecutive_errors >= 5:
                        raise
                    await asyncio.sleep(min(interval * 2, max_poll_interval))
                    continue
                raise

            status = body.get("status", "unknown")
            progress = int(body.get("progress") or 0)
            if progress != last_progress and on_progress:
                last_progress = progress
                result = on_progress(progress, status)
                if asyncio.iscoroutine(result):
                    await result

            if status in STATUS_DONE:
                return self._to_result(body, video_id)
            if status in STATUS_FAILED:
                error = body.get("error") or {}
                raise AgnesVideoError(
                    error.get("message") if isinstance(error, dict) else str(error)
                    or "Video generation failed",
                    video_id=video_id,
                )
            if status not in STATUS_PENDING:
                # Unknown status: surface it rather than spinning forever.
                raise AgnesVideoError(
                    f"Unexpected task status {status!r} for {video_id}",
                    video_id=video_id,
                )

            await asyncio.sleep(interval)
            interval = min(interval * 1.5, max_poll_interval)

        raise AgnesVideoError(
            f"Timed out after {max_wait:.0f}s waiting for task {video_id}",
            video_id=video_id,
        )

    async def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        mode: Optional[str] = None,
        seconds: Any = 5,
        size: str = "720P",
        aspect_ratio: str = "16:9",
        seed: Optional[int] = None,
        first_frame: Optional[str] = None,
        last_frame: Optional[str] = None,
        images: Optional[List[str]] = None,
        audios: Optional[List[str]] = None,
        videos: Optional[List[Dict[str, Any]]] = None,
        output_dir: Optional[str] = None,
        filename: Optional[str] = None,
        max_wait: float = 900.0,
        on_progress: Optional[Callable[[int, str], None]] = None,
    ) -> AgnesVideoResult:
        """Create a task, wait for it, and optionally download the MP4 locally."""
        payload = self.build_payload(
            prompt,
            model=model,
            mode=mode,
            seconds=seconds,
            size=size,
            aspect_ratio=aspect_ratio,
            seed=seed,
            first_frame=first_frame,
            last_frame=last_frame,
            images=images,
            audios=audios,
            videos=videos,
        )
        created = await self.create_task(payload)
        video_id = created["video_id"]

        result = await self.wait_for_completion(
            video_id,
            model_name=payload["model"],
            max_wait=max_wait,
            on_progress=on_progress,
        )

        if output_dir and result.url:
            result.local_path = str(
                await self.download(result.url, output_dir, filename or f"{video_id}.mp4")
            )
        return result

    async def download(self, url: str, output_dir: str, filename: str) -> Path:
        """Stream a completed video to disk.

        The result file is served from a separate CDN host
        (platform-outputs.agnes-ai.space). Reusing the authenticated client
        sends the Bearer token along and the CDN answers 401, so downloads use a
        dedicated client with no default headers.
        """
        dest_dir = Path(output_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as bare:
            async with bare.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=1 << 16):
                        fh.write(chunk)
        return dest

    # -- diagnostics -------------------------------------------------------- #

    async def health(self) -> Dict[str, Any]:
        """Lightweight reachability + credential check."""
        info: Dict[str, Any] = {
            "provider": "agnes",
            "base_url": self.base_url,
            "model": self.model,
            "configured": bool(self.api_key),
            "available": False,
        }
        if not self.api_key:
            info["error"] = "AGNES_API_KEY is not set"
            return info
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/models", timeout=10.0)
            info["http_status"] = resp.status_code
            info["available"] = resp.status_code == 200
            if resp.status_code >= 400:
                info["error"] = self._error_message(
                    resp.status_code, self._json_or_empty(resp)
                )
        except Exception as exc:  # network / DNS / timeout
            info["error"] = str(exc)
        return info

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/models", timeout=15.0)
            resp.raise_for_status()
            return self._json_or_empty(resp).get("data", [])
        except Exception:
            # The model catalogue is static and small; fall back to it so the UI
            # can still render a list when /models is unavailable.
            return [{"id": m, "object": "model"} for m in MODELS]

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _json_or_empty(resp: httpx.Response) -> Dict[str, Any]:
        try:
            data = resp.json()
            return data if isinstance(data, dict) else {"raw": data}
        except Exception:
            return {"raw_text": resp.text[:500]}

    @staticmethod
    def _request_id(resp: httpx.Response, body: Dict[str, Any]) -> Optional[str]:
        error = body.get("error")
        if isinstance(error, dict) and error.get("request_id"):
            return error["request_id"]
        return resp.headers.get("x-request-id") or resp.headers.get("cf-ray")

    @staticmethod
    def _error_message(status_code: int, body: Dict[str, Any]) -> str:
        error = body.get("error")
        if isinstance(error, dict):
            msg = error.get("message") or error.get("code") or str(error)
        else:
            msg = body.get("message") or body.get("raw_text") or str(body)
        hint = {
            400: "Check that mode/media combination, seconds ('4'-'12' as a string), "
                 "size (720P/960P/2K) and aspect_ratio are all valid.",
            401: "The API key is invalid or expired.",
            403: "The key has no access to this model.",
            404: "Unknown video_id — use the video_id returned by POST /v1/videos.",
            429: "Rate limited; retry with backoff.",
        }.get(status_code)
        return f"Agnes API {status_code}: {msg}" + (f" — {hint}" if hint else "")

    @staticmethod
    def _to_result(body: Dict[str, Any], video_id: str) -> AgnesVideoResult:
        metadata = body.get("metadata") or {}
        url = (
            metadata.get("url")
            or body.get("url")
            or (body.get("output") or {}).get("url")
        )
        return AgnesVideoResult(
            video_id=video_id,
            task_id=body.get("task_id") or body.get("id") or video_id,
            status=body.get("status", "completed"),
            url=url,
            model=body.get("model"),
            seconds=body.get("seconds"),
            size=body.get("size"),
            raw=body,
        )

    @staticmethod
    def random_seed() -> int:
        return random.randint(0, 2**32 - 1)


# --------------------------------------------------------------------------- #
# Singleton accessor
# --------------------------------------------------------------------------- #

_client: Optional[AgnesVideoClient] = None


def get_agnes_client(
    api_key: Optional[str] = None, base_url: Optional[str] = None, model: Optional[str] = None
) -> AgnesVideoClient:
    """Return the shared client, rebuilding it when overrides are supplied."""
    global _client
    if api_key or base_url or model:
        return AgnesVideoClient(api_key=api_key, base_url=base_url, model=model)
    if _client is None:
        _client = AgnesVideoClient()
    return _client
