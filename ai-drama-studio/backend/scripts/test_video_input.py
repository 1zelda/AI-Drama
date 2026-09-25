"""video_input 节点 + 关键帧重绘配对的离线行为自检。

用法：
    cd ai-drama-studio/backend
    python scripts/test_video_input.py

不联网、不需要 ffmpeg：postprod 的 probe / extract_frames / extract_audio / cut_clip
全部换成只记录入参的桩，供应商同理。魔改线（把一段现成视频改成"印度人拍的龙珠"）
就靠这几条约定成立：

  1. source 认 asset id / /media/assets 链接 / 绝对路径 / 相对 backend 的路径；
  2. http(s) 直链明确拒绝并指向上传接口（宁可不给，也不塞一个没验证过的下载器）；
  3. ffmpeg 不在 PATH 时报错说清去哪配；
  4. 产物 payload 不许有 paths —— 它同时含视频和帧，_collect_media 先看 paths，
     留着就会把 .mp4 当参考图喂给生图模型；
  5. role=video/image/audio 各取到该取的东西；
  6. init_image_from 按 foreach 下标配帧（第 i 镜改第 i 帧），配不到就退回文字重画；
  7. postprocess 的 bgm_from 能把原片音轨垫进成片；
  8. llm 节点的模板能解析 {{节点id.字段}}（这条是顺带补的回归：以前 llm 节点自己
     写了一遍替换，上游引用全以原样大括号进提示词）；
  9. llm 节点挂 images_from 就改走多模态通道，看不到图时给出的报错能指回 ffmpeg；
  10. video 节点的 source_video_from 按下标取源片段，云端通道会自动发布成公网 URL，
      comfyui 保持本地路径（它和后端在同一台机器上）；
  11. 百炼（dashscope）的请求体形状：video_edit 用 video + reference_image，
      图生视频用 first_frame / last_frame，缺源视频或源视频是本机文件时报错说清缺什么。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
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
            pass
    _stub("openai", AsyncOpenAI=AsyncOpenAI, OpenAI=object)

try:
    import dotenv  # noqa: F401
except ImportError:
    _stub("dotenv", load_dotenv=lambda *a, **kw: False)

from app.orchestrator.engine import WorkflowConfig, WorkflowOrchestrator  # noqa: E402
from app.orchestrator import engine as eng  # noqa: E402
from app.orchestrator.engine import WorkflowOrchestrator as _O  # noqa: E402
from app.services import postprod as pp  # noqa: E402

FAILS: list = []
PASSED = 0


def expect(ok: bool, label: str) -> None:
    global PASSED
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if ok:
        PASSED += 1
    else:
        FAILS.append(label)


# --------------------------------------------------------------------------- #
# 桩
# --------------------------------------------------------------------------- #

class FakeImageResult:
    def __init__(self, path: str):
        self.path = path

    def to_dict(self):
        return {"path": self.path, "local_path": self.path, "urls": []}


class RecordingImageProvider:
    """只记录 reference_images，产出一张假图。"""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.seen: list = []

    async def generate(self, prompt, **kw):
        self.seen.append(kw.get("reference_images"))
        out = self.tmp / f"gen_{len(self.seen):02d}.png"
        out.write_bytes(b"png")
        return FakeImageResult(str(out))


def install_stubs(tmp: Path, *, frames=4, duration=12.0, has_audio=True,
                  ffmpeg_ok=True, audio_path=True):
    calls: dict = {}

    async def fake_probe(path):
        calls["probe"] = str(path)
        return {"path": str(path), "duration": duration, "width": 1920, "height": 1080,
                "fps": 25.0, "has_audio": has_audio, "video_codec": "h264"}

    async def fake_extract_frames(video, dest_dir, count=4, max_side=0, at=None):
        calls["extract"] = {"count": count, "at": at, "max_side": max_side}
        out = Path(dest_dir)
        out.mkdir(parents=True, exist_ok=True)
        n = len(at) if at else count
        got = []
        for i in range(n):
            p = out / f"frame_{i + 1:02d}.jpg"
            p.write_bytes(b"jpg")
            got.append({"path": str(p), "time": round(i * 3.0, 3)})
        return got

    async def fake_extract_audio(video, dest):
        calls["extract_audio"] = str(dest)
        if audio_path is False:
            return None
        p = Path(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"m4a")
        return str(p)

    async def fake_cut_clip(video, dest, start, end):
        calls.setdefault("cuts", []).append((round(start, 2), round(end, 2)))
        p = Path(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"mp4")
        return str(p)

    pp.probe = fake_probe
    pp.extract_frames = fake_extract_frames
    pp.extract_audio = fake_extract_audio
    pp.cut_clip = fake_cut_clip
    pp.ffmpeg_ready = lambda: (ffmpeg_ok, "" if ffmpeg_ok else
                               "找不到 ffmpeg、ffprobe。装上它，或在 .env 里写 "
                               "FFMPEG_BIN / FFPROBE_BIN 指到绝对路径。")
    return calls


def make_orch(tmp: Path, nodes: dict, name: str = "_selftest-vin") -> WorkflowOrchestrator:
    path = tmp / f"{name}.json"
    path.write_text(json.dumps({"name": name, "nodes": nodes}, ensure_ascii=False),
                    encoding="utf-8")
    orch = WorkflowOrchestrator(WorkflowConfig(str(path)), output_dir=str(tmp / "output"))
    orch.context = {"project_id": "_selftest_vin"}
    return orch


async def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "source.mp4"
        src.write_bytes(b"mp4" * 100)

        # ---- 1) source 的四种写法 ----
        orch = make_orch(tmp, {"source_video": {"type": "video_input"}})
        r = orch._resolve_source_media(str(src), "source_video")
        expect(str(r) == str(src), "绝对路径直接可用")

        (BACKEND / "data").mkdir(exist_ok=True)
        (BACKEND / "data" / "_vin_rel.mp4").write_bytes(b"x")
        rel = orch._resolve_source_media("data/_vin_rel.mp4", "source_video")
        expect(str(rel) == str(BACKEND / "data" / "_vin_rel.mp4"), "相对 backend 的路径能解析")
        (BACKEND / "data" / "_vin_rel.mp4").unlink()

        asset_dir = BACKEND / "data" / "assets"
        created_dir = not asset_dir.exists()
        asset_dir.mkdir(parents=True, exist_ok=True)
        index_file = asset_dir / "index.json"
        backup = index_file.read_text(encoding="utf-8") if index_file.exists() else None
        real_file = asset_dir / "ab12cd34ef_片段.mp4"
        real_file.write_bytes(b"y")
        index_file.write_text(json.dumps({"ab12cd34ef": {"id": "ab12cd34ef",
                                                         "path": str(real_file)}},
                                         ensure_ascii=False), encoding="utf-8")
        got = orch._resolve_source_media("ab12cd34ef", "source_video")
        expect(str(got) == str(real_file), "素材库 asset id 能解析成落盘路径")
        got2 = orch._resolve_source_media("/media/assets/ab12cd34ef_片段.mp4", "source_video")
        expect(str(got2) == str(real_file), "/media/assets 链接能反解回文件")

        try:
            orch._resolve_source_media("https://example.com/a.mp4", "source_video")
            expect(False, "http 直链应当被拒绝")
        except ValueError as exc:
            expect("上传" in str(exc), "http 直链拒绝时报错指向上传接口")
        try:
            orch._resolve_source_media("没有这个东西.mp4", "source_video")
            expect(False, "找不到文件应当报错")
        except ValueError as exc:
            expect("试过" in str(exc), "找不到源视频时报错列出尝试过的路径")

        if backup is not None:
            index_file.write_text(backup, encoding="utf-8")
        elif index_file.exists():
            index_file.unlink()
        real_file.unlink()
        if created_dir:
            shutil.rmtree(asset_dir, ignore_errors=True)

        # ---- 2) 跑一次节点：产物形状 ----
        calls = install_stubs(tmp)
        cfg = {"type": "video_input", "source": str(src), "frames": 4,
               "extract_audio": True, "clip_seconds": 5}
        orch = make_orch(tmp, {"source_video": cfg})
        out = await orch._execute_video_input("source_video", cfg, {}, {})

        expect("paths" not in out, "payload 不带 paths（否则 .mp4 会被当成参考图）")
        expect(out["videos"] == [str(src)], "videos 里是源视频")
        expect(len(out["images"]) == 4 and all(p.endswith(".jpg") for p in out["images"]),
               "images 是 4 张帧")
        expect(out["frame_times"] == [0.0, 3.0, 6.0, 9.0], "frame_times 与帧一一对应")
        expect(out["duration"] == 12.0 and out["width"] == 1920, "probe 结果透传")
        expect(out["has_audio"] is True, "has_audio 透传")
        expect(len(out["clips"]) == 3, "clip_seconds=5 把 12s 切成 3 段")
        expect(out["audios"] and Path(out["audios"][0]).exists(), "extract_audio 产出音轨")

        # 源片没有音轨：不该造一个不存在的文件骗下游
        install_stubs(tmp, audio_path=False)
        out_noaudio = await orch._execute_video_input("source_video", cfg, {}, {})
        expect("audios" not in out_noaudio, "抽不到音轨时 payload 里不留 audios")
        install_stubs(tmp)

        collect = _O._collect_media
        expect(collect(out, role="video") == out["clips"],
               "切了片段时 role=video 取切片（下游只改这一小段）")
        expect(collect(out, role="image") == out["images"], "role=image 取到帧而不是 mp4")
        expect(collect(out, role="audio") == out["audios"], "role=audio 取到音轨")

        cfg_nocut = {"type": "video_input", "source": str(src), "frames": 3}
        out_nocut = await orch._execute_video_input("source_video", cfg_nocut, {}, {})
        expect(collect(out_nocut, role="video") == [str(src)],
               "没切片时 role=video 取到整条源视频")

        # ---- 3) 精确给时间戳 ----
        calls = install_stubs(tmp)
        cfg2 = {"type": "video_input", "source": str(src), "frame_times": [0.5, 7.2]}
        await orch._execute_video_input("source_video", cfg2, {}, {})
        expect(calls["extract"]["at"] == [0.5, 7.2], "frame_times 原样传给抽帧")

        # ---- 4) 没有源视频 / 没有 ffmpeg ----
        try:
            await orch._execute_video_input("source_video", {"type": "video_input"}, {}, {})
            expect(False, "空 source 应当报错")
        except ValueError as exc:
            expect("source_video" in str(exc), "没给源视频时报错说出该填哪个键")

        install_stubs(tmp, ffmpeg_ok=False)
        try:
            await orch._execute_video_input("source_video", cfg, {}, {})
            expect(False, "缺 ffmpeg 应当报错")
        except ValueError as exc:
            expect("FFMPEG_BIN" in str(exc), f"缺 ffmpeg 时告诉去哪配：{str(exc)[:40]}…")
        install_stubs(tmp)

        # ---- 5) init_image_from 逐镜配帧 ----
        from app.providers import image_providers as ip

        rec = RecordingImageProvider(tmp / "gen")
        rec_dir = tmp / "gen"
        rec_dir.mkdir(exist_ok=True)
        ip.get_image_provider = lambda *a, **kw: rec
        eng.pick = lambda modality, want=None, **kw: {"provider": "comfyui_image",
                                                      "fallback_from": None}
        cfg3 = {"type": "video_input", "source": str(src), "frames": 4}
        orch3 = make_orch(tmp, {"source_video": cfg3,
                                "key_frames": {"type": "image", "prompt": "p",
                                               "init_image_from": "source_video"}})
        vin = await orch3._execute_video_input("source_video", cfg3, {}, {})

        for i in (0, 2):
            await orch3._execute_image("key_frames", {"type": "image", "prompt": "p",
                                                      "init_image_from": "source_video"},
                                       {"source_video": vin}, {"index": i})
        expect(rec.seen[0] == [vin["images"][0]], "第 0 镜挂第 0 帧")
        expect(rec.seen[1] == [vin["images"][2]], "第 2 镜挂第 2 帧")

        # 超出帧数的镜头：不该瞎挂，也不该崩
        before = len(rec.seen)
        await orch3._execute_image("key_frames", {"type": "image", "prompt": "p",
                                                  "init_image_from": "source_video"},
                                   {"source_video": vin}, {"index": 9})
        expect(rec.seen[-1] in ([], None), "配不到源帧的镜头不挂参考图")
        expect(before + 1 == len(rec.seen), "配不到也照样出图（按文字重画）")

        # 老配置不受影响：没写 init_image_from 时不挂任何上游媒体
        rec.seen.clear()
        await orch3._execute_image("key_frames", {"type": "image", "prompt": "p"},
                                   {"source_video": vin}, {"index": 0})
        expect(rec.seen[-1] is None, "不配 init_image_from 就不会自动挂源帧")

        # ---- 6) bgm_from：原片音轨垫进成片 ----
        captured: dict = {}

        async def fake_build(segments, dest, opts, on_progress=None):
            captured["segments"] = segments
            captured["bgm"] = opts.bgm
            captured["bgm_loop"] = opts.bgm_loop
            return {"path": dest, "duration": 12.0}

        pp.build_video = fake_build
        clip = tmp / "shot1.mp4"
        clip.write_bytes(b"mp4")
        vin_audio = {"node_id": "source_video", "videos": [str(src)],
                     "audios": out["audios"]}
        bcfg = {"type": "postprocess", "from": ["shot_videos"], "bgm_from": "source_video",
                "filename": "_vin_final.mp4"}
        await orch._execute_postprocess("final_cut", bcfg,
                                        {"shot_videos": {"paths": [str(clip)]},
                                         "source_video": vin_audio}, {})
        expect(captured.get("bgm") == out["audios"][0], "bgm_from 把原片音轨垫进成片")
        expect(captured.get("bgm_loop") is False,
               "原片音轨默认不循环（成片比源视频长时不会听见台词重播一遍）")

        captured.clear()
        await orch._execute_postprocess("final_cut", {"type": "postprocess",
                                                      "from": ["shot_videos"],
                                                      "bgm": out["audios"][0],
                                                      "filename": "_vin_final.mp4"},
                                        {"shot_videos": {"paths": [str(clip)]},
                                         "source_video": vin_audio}, {})
        expect(captured.get("bgm_loop") is True, "自己指定的 BGM 仍然循环铺满")

        captured.clear()
        await orch._execute_postprocess("final_cut", {"type": "postprocess",
                                                      "from": ["shot_videos"],
                                                      "filename": "_vin_final.mp4"},
                                        {"shot_videos": {"paths": [str(clip)]},
                                         "source_video": vin_audio}, {})
        expect(captured.get("bgm") is None, "不写 bgm_from 就不会自动带原片音轨")

        # ---- 7) llm 节点的模板渲染：上游字段引用 + 多模态通道 ----
        from app.llm import planner

        seen: dict = {}

        async def fake_chat(**kw):
            seen["messages"] = kw["messages"][0]["content"]
            return json.dumps({"ok": True}, ensure_ascii=False)

        async def fake_vision(prompt, images, model=None, max_tokens=800):
            seen["prompt"] = prompt
            seen["images"] = list(images)
            seen["max_tokens"] = max_tokens
            return '{"ok": true}'

        planner.chat_completion = fake_chat
        planner.vision_completion = fake_vision

        tmpl = BACKEND / "config" / "prompts" / "scripts" / "_selftest_render.yaml"
        tmpl.write_text(
            "name: t\ntype: llm\nprompt: |\n"
            "  扁平：{{title}}\n"
            "  上游：{{style_bible.style_token}}\n"
            "  数组：{{reverse_prompt.shots.0.action}}\n"
            "  不存在：{{nope.field}}\n",
            encoding="utf-8")
        try:
            deps = {"style_bible": {"style_token": "shot on 35mm, single window key"},
                    "reverse_prompt": {"shots": [{"action": "推门进来"}]}}
            orch4 = make_orch(tmp, {"x": {"type": "llm", "prompt_template": "_selftest_render.yaml"}})
            orch4.context = {"project_id": "_selftest_vin", "title": "宝莱坞版"}
            await orch4._execute_llm("x", {"type": "llm",
                                           "prompt_template": "_selftest_render.yaml"},
                                     deps, {"title": "宝莱坞版", **deps})
            msg = seen["messages"]
            expect("扁平：宝莱坞版" in msg, "llm 模板里的扁平键能替换")
            expect("上游：shot on 35mm, single window key" in msg,
                   "llm 模板里的 {{节点id.字段}} 能解析（以前会留下大括号）")
            expect("数组：推门进来" in msg, "llm 模板能取上游数组里的某一项")
            expect("{{nope.field}}" in msg, "解析不到的引用保持原样而不是变成空")

            seen.clear()
            btmpl = {"type": "llm", "prompt_template": "_selftest_render.yaml",
                     "images_from": "input_clip"}
            await orch4._execute_llm("x", btmpl, {**deps, "input_clip": vin},
                                     {"title": "t", "input_clip": vin, **deps})
            expect(seen.get("images") == vin["images"][:4],
                   "images_from 让 llm 节点改走多模态，送进去的是关键帧")
            expect(seen.get("max_tokens", 0) >= 800, "多模态反推的 max_tokens 可配")

            try:
                await orch4._execute_llm("x", {"type": "llm",
                                               "prompt_template": "_selftest_render.yaml",
                                               "images_from": "empty_node"},
                                         {**deps, "empty_node": {}}, {"title": "t"})
                expect(False, "上游没图时应当报错")
            except ValueError as exc:
                expect("ffmpeg" in str(exc), "看不到图时明说要去检查 video_input/ffmpeg")
        finally:
            tmpl.unlink(missing_ok=True)

        # ---- 8) 整段改风格：source_video_from 配对 + 百炼 video_edit 请求体 ----
        from app.providers import video_providers as vp

        class RecordingVideoProvider:
            name = "fakev"
            needs_public_assets = False

            def __init__(self, out_dir: Path):
                self.out = out_dir
                self.seen: list = []

            async def generate(self, prompt, reference_image=None, duration=5.0, **kw):
                self.seen.append({"prompt": prompt, "reference_image": reference_image,
                                  **kw})
                dest = self.out / f"{kw.get('filename') or 'shot'}.mp4"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"mp4")
                return vp.VideoResult(url="https://x/y.mp4", local_path=str(dest),
                                      duration=duration, provider=self.name)

        rvp = RecordingVideoProvider(tmp / "vid")
        vp.get_provider = lambda name=None, **kw: rvp
        vp.PROVIDERS = dict(vp.PROVIDERS, fakev=RecordingVideoProvider)
        eng.pick = lambda kind, name=None: {"provider": name or "fakev"}
        vcfg = {"type": "video", "provider": "fakev", "mode": "video_edit",
                "prompt": "p", "source_video_from": "input_clip", "seconds": 5}
        orch5 = make_orch(tmp, {"video_edit": vcfg})

        deps5 = {"input_clip": out}
        await orch5._execute_video("video_edit", vcfg, deps5,
                                   {"index": 1, "input_clip": out})
        expect(rvp.seen[-1]["source_video"] == out["clips"][1],
               "source_video_from 按下标取到对应切片（一镜改一段）")

        rvp.seen.clear()
        await orch5._execute_video("video_edit", vcfg, deps5, {"input_clip": out})
        expect(rvp.seen[-1]["source_video"] == out["clips"][0],
               "没有 foreach 下标时用整段/第一段，不报错")

        rvp.seen.clear()
        await orch5._execute_video("video_edit", {"type": "video", "provider": "fakev",
                                                  "prompt": "p"},
                                  deps5, {})
        expect(rvp.seen[-1]["source_video"] is None, "不写 source_video_from 就不传源视频")

        emitted: list = []
        orch5._emit = lambda event, **kw: emitted.append(kw.get("status") or "")

        from app.providers import asset_host as ah

        class FakeHost:
            configured = True

            def publish(self, path, subdir=None):
                return f"https://pub.example/{Path(str(path)).name}"

        real_host = ah.get_asset_host
        ah.get_asset_host = lambda: FakeHost()
        rvp.seen.clear()
        await orch5._execute_video("video_edit", vcfg, deps5, {"index": 0, "input_clip": out})
        expect(rvp.seen[-1]["source_video"] ==
               f"https://pub.example/{Path(out['clips'][0]).name}",
               "云端通道的本地源视频先经 PUBLIC_ASSET_BASE_URL 发布成 URL")

        ah.get_asset_host = lambda: types.SimpleNamespace(configured=False, publish=None)
        emitted.clear()
        await orch5._execute_video("video_edit", vcfg, deps5, {"index": 0, "input_clip": out})
        expect(any("PUBLIC_ASSET_BASE_URL" in s for s in emitted),
               "没配公网地址时先在进度里说清楚，而不是让供应商报一个看不懂的错")

        ah.get_asset_host = lambda: FakeHost()
        emitted.clear()
        await orch5._execute_video("clip_edit", dict(vcfg, provider="comfyui"),
                                   deps5, {"index": 0, "input_clip": out})
        expect(rvp.seen[-1]["source_video"] == out["clips"][0] and
               not any("PUBLIC_ASSET" in s for s in emitted),
               "comfyui 和后端在同一台机器上，源视频保持本地路径、不发公网")
        ah.get_asset_host = real_host

        # 百炼请求体：形状照官方（input.media / parameters），字段来自已核对的参考实现
        expect(vp._is_video_edit_model("wan2.7-videoedit")
               and vp._is_video_edit_model("wan2.7-video-edit")
               and not vp._is_video_edit_model("wan2.7-i2v"), "videoedit 型号识别")

        dsp = vp.DashscopeVideoProvider(api_key="k")
        m = dsp._media_for("video_edit", None, None, "https://pub/clip.mp4")
        expect(m == [{"type": "video", "url": "https://pub/clip.mp4"}],
               "video_edit 的 media 是整段源视频")
        m2 = dsp._media_for("video_edit", str(out["images"][0]), None, "https://pub/clip.mp4")
        expect([e["type"] for e in m2] == ["video", "reference_image"]
               and m2[1]["url"].startswith("data:image/"),
               "重绘出来的风格帧作为 reference_image 一起送，本地图片转 data URI")
        m3 = dsp._media_for("first_last_frame", str(out["images"][0]),
                            str(out["images"][1]), None)
        expect([e["type"] for e in m3] == ["first_frame", "last_frame"],
               "普通图生视频用首尾帧两种 media 类型，不会误带源视频")
        try:
            dsp._media_for("video_edit", None, None, None)
            expect(False, "video_edit 缺源视频应当报错")
        except ValueError as exc:
            expect("source_video_from" in str(exc), "缺源视频时点名要填哪个键")
        try:
            dsp._media_for("video_edit", None, None, str(src))
            expect(False, "本地路径当源视频应当被拒")
        except ValueError as exc:
            expect("PUBLIC_ASSET_BASE_URL" in str(exc), "本地源视频告诉你去配公网地址")
        expect(vp._ds_base_url("https://dashscope.aliyuncs.com") ==
               "https://dashscope.aliyuncs.com/api/v1"
               and vp._ds_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1") ==
               "https://dashscope.aliyuncs.com/api/v1",
               "base_url 填 host 或带后缀都能归一")

        print(f"\n通过 {PASSED} 项，失败 {len(FAILS)} 项")
        for f in FAILS:
            print(f"  ✗ {f}")
        print("结果：" + ("✅ video_input 与关键帧配对符合约定" if not FAILS else "❌ 有失败项"))
        return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
