"""Provider package.

Imports are intentionally tolerant: a provider whose optional dependency is
missing is skipped rather than breaking the whole backend on startup.
"""
from .comfyui import ComfyUIClient, ComfyUIError

__all__ = ["ComfyUIClient", "ComfyUIError", "AgnesVideoClient", "AgnesVideoError"]

try:
    from .image_providers import (  # noqa: F401
        BaseImageProvider,
        ComfyUIImageProvider,
        HttpImageProvider,
        ImageResult,
        get_image_provider,
        list_image_providers,
    )

    __all__ += [
        "BaseImageProvider",
        "ComfyUIImageProvider",
        "HttpImageProvider",
        "ImageResult",
        "get_image_provider",
        "list_image_providers",
    ]
except ImportError:  # pragma: no cover
    pass

try:
    from .agnes import AgnesVideoClient, AgnesVideoError, AgnesVideoResult

    __all__ += ["AgnesVideoResult"]
except ImportError:  # pragma: no cover - httpx missing
    pass

try:
    from .character_manager import CharacterManager

    __all__ += ["CharacterManager"]
except ImportError:  # pragma: no cover
    pass

try:
    from .video_providers import (
        AgnesProvider,
        BaseVideoProvider,
        KlingProvider,
        VideoResult,
        get_provider,
        list_providers,
    )

    __all__ += [
        "AgnesProvider",
        "BaseVideoProvider",
        "KlingProvider",
        "VideoResult",
        "get_provider",
        "list_providers",
    ]
except ImportError:  # pragma: no cover
    pass

try:
    from .asset_host import AssetHost, AssetHostError, get_asset_host

    __all__ += ["AssetHost", "AssetHostError", "get_asset_host"]
except ImportError:  # pragma: no cover
    pass
