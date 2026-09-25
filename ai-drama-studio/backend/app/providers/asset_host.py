"""Makes locally generated assets reachable by the Agnes API.

Agnes requires every `first_frame` / `last_frame` / `images` entry to be a
**publicly reachable** URL, and it exposes no file-upload endpoint (verified:
POST /v1/files returns 400 — it is routed to the chat-completions handler).

So a ComfyUI keyframe sitting on disk has to be published before Agnes can use
it. This module copies the file into the backend's static directory and returns
`<PUBLIC_ASSET_BASE_URL>/static/assets/<name>`.

Point PUBLIC_ASSET_BASE_URL at a tunnel in front of this backend, e.g.:

    ngrok http 8000            -> https://abcd-1234.ngrok-free.app
    cloudflared tunnel --url http://localhost:8000
    frpc  (any subdomain you control)

Without a tunnel, keyframe/reference generation cannot work — Agnes will reject
the private URL. `text` mode still works fine, so callers can degrade gracefully.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

_BACKEND_ROOT = Path(__file__).resolve().parents[2]          # .../backend
DEFAULT_ASSET_DIR = _BACKEND_ROOT / "static" / "assets"      # served at /static/assets
STATIC_URL_PREFIX = "/static/assets"

TUNNEL_HELP = (
    "素材需要公网 URL 才能交给 Agnes，但尚未配置 PUBLIC_ASSET_BASE_URL。\n"
    "请任选一种方式暴露本机后端（假设后端端口 8000）：\n"
    "  · ngrok       ngrok http 8000\n"
    "  · cloudflared cloudflared tunnel --url http://localhost:8000\n"
    "  · frp         frpc（使用你自己的子域名）\n"
    "然后在 .env 中设置 PUBLIC_ASSET_BASE_URL=https://你的公网域名\n"
    "若暂时不需要首帧/尾帧，可改用 mode=text（纯文生视频）绕过此限制。"
)


class AssetHostError(RuntimeError):
    pass


class AssetHost:
    """Publishes local files under the backend static dir and returns their URL."""

    def __init__(
        self,
        public_base_url: Optional[str] = None,
        asset_dir: Optional[Path] = None,
        url_prefix: str = STATIC_URL_PREFIX,
    ):
        import os
        from dotenv import load_dotenv

        load_dotenv(_BACKEND_ROOT.parent / ".env")
        self.public_base_url = (
            public_base_url or os.getenv("PUBLIC_ASSET_BASE_URL", "")
        ).rstrip("/")
        self.asset_dir = Path(asset_dir or os.getenv("ASSET_DIR") or DEFAULT_ASSET_DIR)
        self.url_prefix = url_prefix.strip("/")

    # -- state -------------------------------------------------------------- #

    @property
    def configured(self) -> bool:
        return bool(self.public_base_url)

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "public_base_url": self.public_base_url or None,
            "asset_dir": str(self.asset_dir),
            "url_prefix": f"/{self.url_prefix}",
            "asset_count": self.count(),
            "help": None if self.configured else TUNNEL_HELP,
        }

    def count(self) -> int:
        try:
            return sum(1 for p in self.asset_dir.iterdir() if p.is_file())
        except OSError:
            return 0

    # -- publishing --------------------------------------------------------- #

    def publish(self, local_path, subdir: Optional[str] = None, filename: Optional[str] = None) -> str:
        """Copy a local file into the static dir and return its public URL."""
        src = Path(local_path)
        if not src.is_file():
            raise AssetHostError(f"素材文件不存在: {src}")

        target_dir = self.asset_dir / subdir if subdir else self.asset_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        name = filename or self._unique_name(src)
        dest = target_dir / name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)

        return self.url_for(dest)

    def url_for(self, path) -> str:
        """Build the public URL for a file already inside the asset dir."""
        if not self.configured:
            raise AssetHostError(TUNNEL_HELP)
        p = Path(path)
        try:
            relative = p.resolve().relative_to(self.asset_dir.resolve())
        except ValueError:
            # File lives outside the asset dir; fall back to its name.
            relative = Path(p.name)
        return f"{self.public_base_url}/{self.url_prefix}/{relative.as_posix()}"

    def publish_many(self, paths, subdir: Optional[str] = None) -> list:
        return [self.publish(p, subdir=subdir) for p in paths]

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _unique_name(src: Path) -> str:
        """Content-addressed-ish name; keeps URLs stable for identical inputs."""
        digest = hashlib.sha1(src.read_bytes()).hexdigest()[:12]
        stamp = datetime.now().strftime("%Y%m%d")
        return f"{stamp}-{digest}{src.suffix.lower() or '.png'}"

    def resolve(self, value) -> Optional[str]:
        """Accept either a local path or an already-public URL and normalise it.

        Returns None for empty input. Raises AssetHostError when a local path is
        given but no tunnel is configured.
        """
        if not value:
            return None
        text = str(value)
        if text.startswith(("http://", "https://")):
            return text
        return self.publish(text)


_host: Optional[AssetHost] = None


def get_asset_host() -> AssetHost:
    global _host
    if _host is None:
        _host = AssetHost()
    return _host
