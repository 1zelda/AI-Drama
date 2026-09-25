"""Agnes AI video generation provider.

Uses the local Agnes proxy at http://127.0.0.1:57324
which routes to the official Agnes API at apihub.agnes-ai.com.
"""
from __future__ import annotations
import os
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any
import httpx

from dotenv import load_dotenv
load_dotenv()

AGNES_PROXY_URL = os.getenv("AGNES_PROXY_URL", "http://127.0.0.1:57324")
AGNES_API_KEY = os.getenv("AGNES_API_KEY", "")
DEFAULT_MODEL = os.getenv("AGNES_VIDEO_MODEL", "agnes-video-2.5-flash")
DEFAULT_MODE = os.getenv("AGNES_VIDEO_MODE", "T2V")


class AgnesVideoError(Exception):
    pass


class AgnesVideoClient:
    """Client for Agnes video generation via the local proxy."""

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key or AGNES_API_KEY
        self.base_url = (base_url or AGNES_PROXY_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.mode = DEFAULT_MODE
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=120.0,
            )
        return self._client

    async def generate(
        self,
        prompt: str,
        *,
        model: str = None,
        resolution: str = "720p",
        duration: int = 5,
        mode: str = None,
        seed: int = None,
        output_dir: str = None,
    ) -> Dict[str, Any]:
        """Generate a video from a text prompt. Returns dict with video_url and status."""
        client = await self._get_client()
        params = {
            "model": model or self.model,
            "prompt": prompt,
            "mode": mode or self.mode,
            "resolution": resolution,
            "duration": duration,
        }
        if seed is not None:
            params["seed"] = seed

        resp = await client.post("/v1/videos", json=params)
        resp.raise_for_status()
        data = resp.json()

        # Agnes API returns a task_id for async generation
        task_id = data.get("id") or data.get("task_id")
        if not task_id:
            # Direct response with video URL
            video_url = data.get("url") or data.get("video_url") or data.get("output", [{}])[0].get("url") if data.get("output") else None
            return {"task_id": None, "video_url": video_url, "status": "completed", "raw": data}

        # Poll for completion
        result = await self._poll_task(client, task_id, output_dir=output_dir)
        return result

    async def _poll_task(
        self, client: httpx.AsyncClient, task_id: str, output_dir: str = None,
        max_wait: int = 300, poll_interval: float = 5.0,
    ) -> Dict[str, Any]:
        """Poll Agnes API until video is ready."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            resp = await client.get(f"/v1/videos/{task_id}")
            resp.raise_for_status()
            status_data = resp.json()
            status = status_data.get("status", "unknown")

            if status in ("completed", "succeeded"):
                video_url = status_data.get("output", {}).get("url") or status_data.get("video_url") or status_data.get("result", {}).get("url")
                return {"task_id": task_id, "video_url": video_url, "status": "completed", "raw": status_data}
            elif status in ("failed", "error"):
                raise AgnesVideoError(f"Video generation failed: {status_data.get('error', status_data)}")
            elif status in ("processing", "queued"):
                await asyncio.sleep(poll_interval)
            else:
                # Return whatever we got
                return {"task_id": task_id, "status": status, "raw": status_data}

        raise AgnesVideoError(f"Timeout waiting for video task {task_id}")

    async def list_models(self) -> list:
        """List available Agnes models."""
        client = await self._get_client()
        resp = await client.get("/v1/models")
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])

    async def health(self) -> bool:
        """Check if Agnes proxy is reachable."""
        try:
            client = await self._get_client()
            resp = await client.get("/health", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


# Singleton instance
_agnes_client: Optional[AgnesVideoClient] = None

def get_agnes_client() -> AgnesVideoClient:
    global _agnes_client
    if _agnes_client is None:
        _agnes_client = AgnesVideoClient()
    return _agnes_client
