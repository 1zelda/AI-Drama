"""多角色分音色 + 定妆图回填 + 口型音频的离线行为自检。

用法：
    cd ai-drama-studio/backend
    python scripts/test_engine_voice.py

不联网、不花钱：edge-tts / GPT-SoVITS / 视频供应商全换成只记录入参的桩。
多人对话戏的配音、角色一致性和口型同步就靠这几条约定成立：

  1. 说话人查音色：角色设定里每个角色的 voice 字段决定谁用哪个嗓子；
  2. 节点里手写 voice_map 能覆盖上游，改配置就能换声；
  3. 表里没有的角色从 voice_pool 稳定取一个（重跑不换嗓子）；
  4. 「角色名：台词」只有名字真在表里才拆前缀，旁白「注意：」不会被误伤；
  5. 什么都没配时行为和旧版完全一致（整段用节点默认音色）——老工作流不受影响；
  6. 定妆图生成后回填角色档案库，重跑只换自己产出的图，用户上传的一张不动；
  7. 视频节点 audio_from 按下标配口型音频，文件不存在就丢掉并说明。
"""
from __future__ import annotations

import asyncio
import json
import shutil
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
from app.providers import audio_providers as ap  # noqa: E402
from app.providers import video_providers as vp  # noqa: E402
from app.providers.character_manager import CharacterManager  # noqa: E402

FAILS: list = []
PASSED = 0
PROJECT = "_selftest_voice"
DATA_DIR = BACKEND / "data"


def expect(ok: bool, label: str) -> None:
    global PASSED
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if ok:
        PASSED += 1
    else:
        FAILS.append(label)


CALLS: list = []


async def fake_edge(text, voice="?", rate="+0%", pitch="+0Hz"):
    CALLS.append({"kind": "edge", "text": text, "voice": voice, "rate": rate, "pitch": pitch})
    return b"ID3fake-mp3"


async def fake_clone(text, ref_audio_path, prompt_text="", prompt_lang="zh", **kw):
    CALLS.append({"kind": "gptsovits", "text": text, "ref": ref_audio_path,
                  "prompt_text": prompt_text})
    return b"RIFFfake-wav"


def make_orch(tmp: Path, nodes: dict, name: str = "_selftest-voice") -> WorkflowOrchestrator:
    doc = {"name": name, "nodes": nodes}
    path = tmp / f"{name}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    orch = WorkflowOrchestrator(WorkflowConfig(str(path)), output_dir=str(tmp / "output"))
    orch.context = {"project_id": PROJECT}
    return orch


async def run_tts(orch, cfg: dict, merged: dict, deps: dict) -> dict:
    CALLS.clear()
    out = await orch._execute_tts("narration", cfg, deps, merged)
    return {"call": CALLS[0] if CALLS else {}, "out": out}


CHARS_DEP = {
    "character_generator": {
        "characters": [
            {"name": "林晚", "image_prompt": "a 26-year-old Chinese woman", "voice": "zh-CN-XiaoyiNeural"},
            {"name": "陈默", "image_prompt": "a 32-year-old Chinese man",
             "voice": {"engine": "gptsovits", "voice_name": "陈默克隆"}},
        ]
    }
}

POOL = ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural"]


async def main() -> int:
    # 桩：TTS 只记录入参
    ap.tts_edge = fake_edge
    ap.tts_gptsovits = fake_clone
    ap.list_voices = lambda: [{"name": "陈默克隆", "ref_audio": "F:/fake/chen.mp3",
                               "prompt_text": "这是我的声音样本", "prompt_lang": "zh"}]

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        shutil.rmtree(DATA_DIR / PROJECT / "characters", ignore_errors=True)

        base = {"type": "tts", "text_field": "line", "voice": "zh-CN-YunxiNeural",
                "voices_from": "character_generator"}
        orch = make_orch(tmp, {"narration": base})

        # 1) 按说话人查表
        r = await run_tts(orch, base, {"index": 0, "speaker": "林晚", "line": "你先走"}, CHARS_DEP)
        expect(r["call"]["voice"] == "zh-CN-XiaoyiNeural", f"林晚拿到自己表里的音色（{r['call']['voice']}）")
        expect(r["out"]["speaker"] == "林晚" and r["out"]["voice"] == "zh-CN-XiaoyiNeural",
               "产物里记录了说话人和音色，页面上能核对")

        # 2) 克隆音色：一个节点里混用 edge + GPT-SoVITS
        r = await run_tts(orch, base, {"index": 1, "speaker": "陈默", "line": "我等你"}, CHARS_DEP)
        expect(r["call"]["kind"] == "gptsovits" and r["call"]["ref"] == "F:/fake/chen.mp3",
               "陈默走 GPT-SoVITS 克隆音色（同一节点混用两种引擎）")

        # 3) voice_map 覆盖上游
        mapped = {**base, "voice_map": {"林晚": "zh-CN-XiaohanNeural"}}
        r = await run_tts(orch, mapped, {"index": 0, "speaker": "林晚", "line": "好"}, CHARS_DEP)
        expect(r["call"]["voice"] == "zh-CN-XiaohanNeural", "节点写 voice_map 能盖掉角色设定里的音色")

        # 4) 表里没有的角色 → voice_pool 稳定分配，且两个角色不撞声
        pooled = {**base, "voice_pool": POOL}
        r1 = await run_tts(orch, pooled, {"index": 2, "speaker": "王婶", "line": "站住"}, CHARS_DEP)
        r2 = await run_tts(orch, pooled, {"index": 3, "speaker": "王婶", "line": "别跑"}, CHARS_DEP)
        r3 = await run_tts(orch, pooled, {"index": 4, "speaker": "赵警官", "line": "配合一下"}, CHARS_DEP)
        expect(r1["call"]["voice"] in POOL and r1["call"]["voice"] == r2["call"]["voice"],
               "没配音色的角色从 voice_pool 取，且同一角色重跑不换嗓子")
        expect(r3["call"]["voice"] != r1["call"]["voice"], f"不同角色至少不撞声（{r1['call']['voice']} vs {r3['call']['voice']}）")

        # 5) 「角色名：台词」拆前缀；旁白前缀不误伤
        r = await run_tts(orch, pooled, {"index": 5, "line": "林晚：你先走，我随后就到"}, CHARS_DEP)
        expect(r["call"]["text"] == "你先走，我随后就到" and r["call"]["voice"] == "zh-CN-XiaoyiNeural",
               "台词里写「林晚：…」会拆出说话人并剥掉前缀")
        r = await run_tts(orch, pooled, {"index": 6, "line": "注意：本故事纯属虚构"}, CHARS_DEP)
        expect(r["call"]["text"] == "注意：本故事纯属虚构", "「注意：」这类旁白开头不会被当成角色名拆掉")

        # 6) 什么都没配 → 与旧版一致
        legacy = {"type": "tts", "text_field": "subtitle", "voice": "zh-CN-YunxiNeural", "rate": "+8%"}
        orch2 = make_orch(tmp, {"narration": legacy})
        r = await run_tts(orch2, legacy, {"index": 0, "subtitle": "那一夜雨下得很大"}, {})
        expect(r["call"]["voice"] == "zh-CN-YunxiNeural" and r["call"]["rate"] == "+8%"
               and r["call"]["text"] == "那一夜雨下得很大", "不配任何音色字段时行为与旧版完全一致")

        # 7) 定妆图回填角色档案库
        sheets = []
        for i in range(2):
            p = tmp / "output" / "_selftest-reg" / "character_assets" / f"sheet_{i}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x89PNG")
            sheets.append(str(p))
        user_upload = tmp / "user_upload.png"
        user_upload.write_bytes(b"\x89PNG")

        reg = {"type": "image", "prompt": "x", "register_character": True,
               "character_project": PROJECT}
        orch3 = make_orch(tmp, {"character_assets": reg}, name="_selftest-reg")
        orch3._register_character("character_assets", reg,
                                  {"name": "林晚", "role": "女主", "costume": "米色风衣",
                                   "image_prompt": "a 26-year-old Chinese woman"},
                                  {"paths": [sheets[0], str(tmp / "gone.png")]})
        cm = CharacterManager(PROJECT, str(DATA_DIR))
        char = next((c for c in cm.list_characters() if c.name == "林晚"), None)
        expect(char is not None and char.appearance == "a 26-year-old Chinese woman"
               and char.costume == "米色风衣", "档案库里建出了这个角色，锚点取的是 image_prompt")
        expect(char.reference_images == [sheets[0]], f"定妆图进了参考图列表，不存在的图被滤掉（{char.reference_images}）")

        cm.update_character(char.id, reference_images=[str(user_upload)] + char.reference_images)
        orch3._register_character("character_assets", reg, {"name": "林晚"}, {"paths": [sheets[1]]})
        again = CharacterManager(PROJECT, str(DATA_DIR)).get_character(char.id)
        expect(str(user_upload) in again.reference_images, "重跑登记不会动用户上传的参考图")
        expect(sheets[1] in again.reference_images and sheets[0] not in again.reference_images,
               "重跑只换掉本节点自己上一次产出的定妆图")

        # 8) 视频节点的口型音频
        voices = []
        for i in range(2):
            v = tmp / f"voice_{i}.mp3"
            v.write_bytes(b"ID3")
            voices.append(str(v))

        sink: dict = {}

        class FakeProvider:
            name = "fake"
            needs_public_assets = False

            async def generate(self, prompt, reference_image=None, duration=5.0, **kwargs):
                sink.update(kwargs)
                return vp.VideoResult(local_path="clip.mp4", provider="fake", status="completed")

        eng.pick = lambda mod, pref=None, **kw: {"provider": "fake", "fallback_from": None}
        vp.get_provider = lambda name=None, api_key=None, **kw: FakeProvider()
        vcfg = {"type": "video", "prompt": "{{video_prompt}}", "audio_from": "narration"}
        orch4 = make_orch(tmp, {"shot_videos": vcfg}, name="_selftest-audio")
        deps = {"narration": {"paths": voices}}
        await orch4._execute_video("shot_videos", vcfg, deps, {"index": 1, "video_prompt": "p"})
        expect(sink.get("audio") == voices[1], "口型音频按 foreach 下标和这一镜对上")

        sink.clear()
        bad = {"narration": {"paths": [str(tmp / "missing.mp3")]}}
        await orch4._execute_video("shot_videos", vcfg, bad, {"index": 0, "video_prompt": "p"})
        expect(sink.get("audio") is None, "音频文件不存在时丢掉，不把假路径传给供应商")
        expect(any("口型同步" in json.dumps(e, ensure_ascii=False) for e in orch4.events),
               "丢掉的原因进了进度事件")

    shutil.rmtree(DATA_DIR / PROJECT, ignore_errors=True)

    print(f"\n通过 {PASSED} 项，失败 {len(FAILS)} 项")
    if FAILS:
        for f in FAILS:
            print(f"  ✗ {f}")
        print("结果：❌ 分音色/回填链路有问题")
        return 1
    print("结果：✅ 分音色、定妆图回填、口型音频都符合约定")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
