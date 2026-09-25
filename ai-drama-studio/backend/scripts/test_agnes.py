"""End-to-end check for the Agnes Video 2.5 provider.

Usage:
    cd ai-drama-studio/backend
    venv/Scripts/python.exe scripts/test_agnes.py            # full run (costs credits)
    venv/Scripts/python.exe scripts/test_agnes.py --validate  # offline checks only

The validate-only mode exercises parameter validation and the published asset
helper without spending any credit.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.agnes import (  # noqa: E402
    AgnesVideoClient,
    AgnesVideoError,
    get_agnes_client,
)
from app.providers.asset_host import get_asset_host  # noqa: E402

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = ""):
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def expect_error(name: str, fn, *, contains: str = ""):
    try:
        fn()
    except AgnesVideoError as exc:
        ok = contains.lower() in str(exc).lower()
        check(name, ok, str(exc)[:110])
        return
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"unexpected {type(exc).__name__}: {exc}")
        return
    check(name, False, "no error raised")


def validate_only() -> None:
    print("\n== parameter validation (offline) ==")
    c = AgnesVideoClient(api_key="dummy-key-for-validation")

    expect_error("rejects pixel size 1280x720", lambda: c.build_payload("p", size="1280x720"), contains="size")
    expect_error("rejects lowercase 720p", lambda: c.build_payload("p", size="720p"), contains="size")
    expect_error("rejects aspect_ratio auto", lambda: c.build_payload("p", aspect_ratio="auto"), contains="aspect_ratio")
    expect_error("rejects aspect_ratio 2:1", lambda: c.build_payload("p", aspect_ratio="2:1"), contains="aspect_ratio")
    expect_error("rejects seconds=3", lambda: c.build_payload("p", seconds=3), contains="seconds")
    expect_error("rejects seconds=20", lambda: c.build_payload("p", seconds=20), contains="seconds")
    expect_error(
        "text mode rejects first_frame",
        lambda: c.build_payload("p", mode="text", first_frame="https://x/y.png"),
        contains="text",
    )
    expect_error(
        "keyframe mode requires a frame",
        lambda: c.build_payload("p", mode="keyframe"),
        contains="keyframe",
    )
    expect_error(
        "keyframe mode rejects images[]",
        lambda: c.build_payload("p", mode="keyframe", first_frame="https://x/y.png", images=["https://x/z.png"]),
        contains="keyframe",
    )
    expect_error(
        "reference mode requires media",
        lambda: c.build_payload("p", mode="reference"),
        contains="reference",
    )

    print("\n== payload shape ==")
    p = c.build_payload("a cat", seconds=5, size="720P", aspect_ratio="9:16", seed=42)
    check("seconds serialised as string", p["seconds"] == "5", repr(p["seconds"]))
    check("n is pinned to 1", p["n"] == 1)
    check("seed forwarded as int", p["seed"] == 42 and isinstance(p["seed"], int))
    check("no forbidden width/height", "width" not in p and "height" not in p)

    p2 = c.build_payload("a cat", first_frame="https://x/first.png")
    check("mode auto-inferred as keyframe", p2["mode"] == "keyframe", p2["mode"])
    p3 = c.build_payload("a cat", images=["https://x/ref.png"])
    check("mode auto-inferred as reference", p3["mode"] == "reference", p3["mode"])
    p4 = c.build_payload("a cat")
    check("mode auto-inferred as text", p4["mode"] == "text", p4["mode"])

    print("\n== asset host ==")
    host = get_asset_host()
    st = host.status()
    print(f"  public_base_url = {st['public_base_url']}")
    print(f"  asset_dir       = {st['asset_dir']}")
    print(f"  configured      = {st['configured']}")
    check("status() exposes help when unconfigured", (not st["configured"]) or st["help"] is None)
    try:
        host.url_for(Path("somewhere.png"))
        check("url_for raises without tunnel only when unconfigured", st["configured"])
    except RuntimeError as exc:
        check("url_for raises a helpful error when unconfigured", not st["configured"], str(exc).splitlines()[0])
    print("  (note: keyframe/reference modes need PUBLIC_ASSET_BASE_URL; text mode does not)")


async def live_run() -> None:
    print("\n== live API ==")
    client = get_agnes_client()
    info = await client.health()
    print(f"  health: {info}")
    check("API key configured", info["configured"])
    check("API reachable", info.get("available", False), info.get("error", ""))
    if not info.get("available"):
        print("  skipping live generation — API unreachable")
        return

    with tempfile.TemporaryDirectory() as tmp:
        result = await client.generate(
            "A paper boat drifting down a lantern-lit canal at night, gentle camera push-in",
            model="agnes-video-2.5-flash",
            seconds=4,
            size="720P",
            aspect_ratio="16:9",
            output_dir=tmp,
            max_wait=300,
            on_progress=lambda p, s: print(f"    progress {p}% ({s})"),
        )
        check("task completed", result.status == "completed", result.status)
        check("video url present", bool(result.url), str(result.url)[:80])
        check("file downloaded", bool(result.local_path) and Path(result.local_path).is_file())
        if result.local_path:
            size_kb = Path(result.local_path).stat().st_size / 1024
            check("downloaded file is non-trivial", size_kb > 20, f"{size_kb:.0f} KB")
    await client.close()


def main():
    print("Agnes Video 2.5 provider check")
    validate_only()
    if "--validate" not in sys.argv:
        asyncio.run(live_run())
    else:
        print("\n(--validate set: live generation skipped)")

    print(f"\n==== {len(PASSED)} passed, {len(FAILED)} failed ====")
    if FAILED:
        print("failed:", ", ".join(FAILED))
        sys.exit(1)


if __name__ == "__main__":
    main()
