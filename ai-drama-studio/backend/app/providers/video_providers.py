"""Video generation providers - cloud API wrappers for Seedance, Kling, Veo."""
from __future__ import annotations
import os
import aiohttp
import asyncio
from typing import Optional, List, Dict
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VideoResult:
    url: str
    duration: float
    format: str
    provider: str


class KlingProvider:
    """Wrapper for Kling AI video generation API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("KLING_API_KEY")
        self.base_url = os.environ.get("KLING_API_URL", "https://api.klingai.com/v1")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 5.0,
        width: int = 832,
        height: int = 480,
    ) -> VideoResult:
        if not self.api_key:
            raise ValueError("KLING_API_KEY not set")
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "kling-v1-6",
                "prompt": prompt,
                "duration": duration,
                "width": width,
                "height": height,
            }
            if reference_image:
                payload["image"] = reference_image
            async with session.post(
                f"{self.base_url}/videos/generations",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            ) as resp:
                result = await resp.json()
                return VideoResult(
                    url=result.get("video_url", ""),
                    duration=duration,
                    format="mp4",
                    provider="kling",
                )


class SeedanceProvider:
    """Wrapper for Google Seedance video generation API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("SEEDANCE_API_KEY")
        self.base_url = os.environ.get("SEEDANCE_API_URL", "https://generativelanguage.googleapis.com/v1beta")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 8.0,
    ) -> VideoResult:
        if not self.api_key:
            raise ValueError("SEEDANCE_API_KEY not set")
        # Seedance API integration placeholder
        return VideoResult(url="", duration=duration, format="mp4", provider="seedance")


class VeoProvider:
    """Wrapper for Google Veo video generation API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("VEO_API_KEY")
        self.base_url = os.environ.get("VEO_API_URL", "https://generativelanguage.googleapis.com/v1beta")

    async def generate(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        duration: float = 8.0,
    ) -> VideoResult:
        if not self.api_key:
            raise ValueError("VEO_API_KEY not set")
        return VideoResult(url="", duration=duration, format="mp4", provider="veo")


# Registry for easy lookup
PROVIDERS = {
    "kling": KlingProvider,
    "seedance": SeedanceProvider,
    "veo": VeoProvider,
}


def get_provider(name: str, api_key: Optional[str] = None):
    cls = PROVIDERS.get(name)
    if not cls:
        raise ValueError(f"Unknown video provider: {name}")
    return cls(api_key)