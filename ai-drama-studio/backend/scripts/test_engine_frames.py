"""首尾帧链路的离线行为自检：只驱动 _execute_video 的取帧与 mode 推导。

用法：
    cd ai-drama-studio/backend
    python scripts/test_engine_frames.py

不联网、不花钱：供应商被换成一个只记录入参的假对象，
验证的是动漫工作流依赖的那几条引擎约定——

  1. 尾帧按 foreach 下标和首帧一一对应（错位会把第一镜的尾帧接到第五镜）；
  2. 下标越界/文件不存在时丢掉尾帧，退回普通图生视频，而不是乱配；
  3. 首尾帧都在时 mode=first_last_frame，缺一帧时 mode=first_frame；
  4. 节点显式写了 mode 时不覆盖它；
  5. 上游尾帧节点被 disabled 跳过（deps 里根本没有这个键）时整条链照常跑。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _stub(name: str, **attrs) -> None:
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


try:
    import openai  # noqa: F401
except ImportError:
    class AsyncOpenAI:  # noqa: N801
        def __init__(self, *a, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

        async def _create(self, *a, **kw):
            raise RuntimeError("测试桩：不应真的调用 LLM")
    _stub("openai", AsyncOpenAI=AsyncOpenAI, OpenAI=object)

try:
    import dotenv  # noqa: F401
except ImportError:
    _stub("dotenv", load_dotenv=lambda *a, **kw: False)

from app.orchestrator import engine as eng  # noqa: E402
from app.orchestrator.engine import WorkflowConfig, WorkflowOrchestrator  # noqa: E402
from app.providers import video_providers as vp  # noqa: E402

FAILS: list = []
PASSED = 0


def expect(ok: bool, label: str) -> None:
    global PASSED
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if ok:
        PASSED += 1
    else:
        FAILS.append(label)


class FakeProvider:
    """只记入参的假视频供应商（和 zhipu/kling 一样用 **kwargs 吞掉不认识的字段）。"""

    name = "fake"
    needs_public_assets = False

    def __init__(self, sink: dict):
        self.sink = sink

    async def generate(self, prompt, reference_image=None, duration=5.0, **kwargs):
        self.sink.update(prompt=prompt, reference_image=reference_image,
                         duration=duration, **kwargs)
        return vp.VideoResult(url="", local_path=self.sink.pop("_out", "clip.mp4"),
                              provider="fake", status="completed")


def make_orch(tmp: Path) -> WorkflowOrchestrator:
    doc = {"name": "_selftest-frames",
           "nodes": {"shot_videos": {"type": "video", "prompt": "{{video_prompt}}"}}}
    wf = tmp / "_selftest-frames.json"
    wf.write_text(json.dumps(doc), encoding="utf-8")
    return WorkflowOrchestrator(WorkflowConfig(str(wf)), output_dir=str(tmp / "output"))


async def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        firsts = []
        tails = []
        for i in range(2):
            f = tmp / f"first_{i}.png"
            f.write_bytes(b"\x89PNG")
            t = tmp / f"tail_{i}.png"
            t.write_bytes(b"\x89PNG")
            firsts.append(str(f))
            tails.append(str(t))

        orch = make_orch(tmp)
        eng.pick = lambda mod, pref=None, **kw: {"provider": "fake", "fallback_from": None}

        deps = {"shot_images": {"paths": firsts}, "shot_end_frames": {"paths": tails}}
        base = {"type": "video", "prompt": "{{video_prompt}}",
                "first_frame_from": "shot_images", "last_frame_from": "shot_end_frames",
                "seconds": 5}

        async def run(cfg: dict, merged: dict, dep: dict | None = None) -> dict:
            sink: dict = {"_out": str(tmp / "clip.mp4")}
            vp.get_provider = lambda name=None, api_key=None, **kw: FakeProvider(sink)
            out = await orch._execute_video("shot_videos", cfg, dep if dep is not None else deps, merged)
            sink.pop("_out", None)
            sink["_payload"] = out
            return sink

        # 1) 正常配对：index=1 → 第 2 张首帧 + 第 2 张尾帧
        r = await run(base, {"index": 1, "video_prompt": "p1"})
        expect(r["reference_image"] == firsts[1], f"首帧按 foreach 下标取（{Path(str(r['reference_image'])).name}）")
        expect(r["last_frame"] == tails[1], "尾帧按同一个下标配对")
        expect(r["mode"] == "first_last_frame", "两张帧都在 → mode=first_last_frame")
        expect("尾帧" in json.dumps([e for e in orch.events if e.get("type") == "node_progress"],
                                   ensure_ascii=False), "进度里报告了用了哪张尾帧")

        # 2) 尾帧节点被 disabled（deps 里没这个键）→ 退回普通图生视频
        r = await run(base, {"index": 0, "video_prompt": "p0"},
                      dep={"shot_images": {"paths": firsts}})
        expect(r["last_frame"] is None, "上游尾帧节点整个缺席时不报错")
        expect(r["mode"] == "first_frame", "缺尾帧 → mode 退回 first_frame")

        # 3) 下标越界不乱配第一张
        r = await run(base, {"index": 7, "video_prompt": "p7"})
        expect(r["last_frame"] is None, "尾帧下标越界时丢掉，而不是拿第一张凑")

        # 4) 尾帧文件不存在 → 丢弃并明说
        ghost = {"shot_images": {"paths": firsts}, "shot_end_frames": {"paths": [str(tmp / "nope.png")]}}
        r = await run(base, {"index": 0, "video_prompt": "p0"}, dep=ghost)
        expect(r["last_frame"] is None, "尾帧文件不存在时丢掉")
        expect(any("尾帧不存在" in json.dumps(e, ensure_ascii=False) for e in orch.events),
               "丢帧原因进了进度")

        # 5) 显式 last_frame / mode 优先，不替用户改
        r = await run({**base, "last_frame": tails[1], "mode": "reference"},
                      {"index": 0, "video_prompt": "p0"})
        expect(r["last_frame"] == tails[1], "节点写死的 last_frame 照用")
        expect(r["mode"] == "reference", "节点写死 mode 时引擎不覆盖")

        # 6) 只有尾帧没有首帧（分镜图那步没产出）→ 不硬凑首尾帧
        r = await run({k: v for k, v in base.items() if k != "first_frame_from"},
                      {"index": 1, "video_prompt": "p1"})
        expect(r["mode"] == "first_frame" or r["mode"] == "text", "缺首帧时不会报 first_last_frame")
        expect(r["last_frame"] == tails[1], "缺首帧时尾帧照样带给供应商")

    print(f"\n通过 {PASSED} 项，失败 {len(FAILS)} 项")
    if FAILS:
        for f in FAILS:
            print(f"  ✗ {f}")
        print("结果：❌ 首尾帧链路有问题")
        return 1
    print("结果：✅ 首尾帧链路符合约定")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
