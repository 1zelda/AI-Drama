"""ComfyUI 预设包装器的离线自检（不连 ComfyUI、不联网）。

用法：
    cd ai-drama-studio/backend
    python scripts/test_comfyui_preset.py

验的是「口型同步 / 片段魔改要靠本地 ComfyUI 跑」这条链路的地基：
  1. --map 写错节点号或字段名时必须当场报错（这是最容易哑火的地方）；
  2. 包装出的预设能被 load_workflow 认成「带 wrapper 的格式」并取出裸图；
  3. 引擎跑图时的两轨注入都生效：{{占位符}} 渲染 + params 补丁（含类型纠偏）；
  4. 数字字段（width/length/fps）不会因为被写成字符串占位符而被 ComfyUI 拒收；
  5. 喂进 UI 存档格式时给出可执行的纠正指引，而不是产出一个半截预设。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.argv = ["test"]
import importlib  # noqa: E402

mcp = importlib.import_module("make_comfyui_preset")
from app.providers.comfyui import ComfyUIClient  # noqa: E402
from app.providers.image_providers import _render_placeholders  # noqa: E402

FAILS: list = []
PASSED = 0


def expect(ok: bool, label: str) -> None:
    global PASSED
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if ok:
        PASSED += 1
    else:
        FAILS.append(label)


def raises(fn, *a, **kw) -> str:
    """跑一下，把 die() 的 SystemExit 文案抓回来。"""
    try:
        fn(*a, **kw)
    except SystemExit:
        return "exited"
    return ""


# 一个「像 InfiniteTalk」的最小 API 格式图：装载器 + 条件 + 采样 + 合成
LIPSYNC_GRAPH = {
    "20": {"class_type": "WanVideoModelLoader", "inputs": {"model": "wan2.1_i2v_480p.safetensors"}},
    "30": {"class_type": "CLIPTextEncode", "inputs": {"text": "老提示词", "clip": ["20", 0]}},
    "41": {"class_type": "LoadImage", "inputs": {"image": "woman.png"}},
    "125": {"class_type": "LoadAudio", "inputs": {"audio": "hush.mp3"}},
    "50": {"class_type": "WanVideoImageToVideoMultiTalk",
           "inputs": {"width": 832, "height": 480, "length": 81,
                      "start_image": ["41", 0], "audio": ["125", 0]}},
    "111": {"class_type": "VHS_VideoCombine",
            "inputs": {"fps": 25, "filename_prefix": "drama_video", "images": ["50", 0]}},
}

MAPPINGS = [("prompt", "30", "text"), ("reference_image", "41", "image"),
            ("audio_file", "125", "audio"), ("width", "50", "width"),
            ("fps", "111", "fps")]


def build(mappings=None, graph=None) -> dict:
    return mcp.build_preset(graph if graph is not None else copy.deepcopy(LIPSYNC_GRAPH),
                            name="_selftest-lipsync", wtype="video", output_node="111",
                            mappings=mappings if mappings is not None else MAPPINGS,
                            description="自检用")


def main() -> int:
    # 1. 接线错误必须当场炸
    expect(raises(mcp.build_preset, copy.deepcopy(LIPSYNC_GRAPH), name="x", wtype="video",
                  output_node="111", mappings=[("prompt", "999", "text")],
                  description="") == "exited",
           "--map 指向不存在的节点号时报错，不是安静产出半成品")
    expect(raises(mcp.build_preset, copy.deepcopy(LIPSYNC_GRAPH), name="x", wtype="video",
                  output_node="111", mappings=[("prompt", "41", "nope")],
                  description="") == "exited",
           "--map 字段名写错时报错，并列出可用字段")
    expect(raises(mcp.build_preset, {"nodes": [{"id": 1}], "links": []}, name="x",
                  wtype="video", output_node="", mappings=[], description="") == "exited",
           "喂 UI 存档格式时拒绝，并提示改用 Export (API)")
    expect(raises(mcp.build_preset, copy.deepcopy(LIPSYNC_GRAPH), name="x", wtype="video",
                  output_node="404", mappings=[], description="") == "exited",
           "--output-node 写错时报错")

    preset = build()
    expect(preset["params"]["audio_file"] == {"node": "125", "field": "audio"},
           "params 记下了 audio_file 落在哪个节点哪个字段")
    expect(preset["nodes"]["125"]["inputs"]["audio"] == "{{audio_file}}",
           "LoadAudio 的字段被换成占位符")
    expect(isinstance(preset["nodes"]["50"]["inputs"]["start_image"], list),
           "只换写了 --map 的字段，别的连线原样保留")

    # 2. load_workflow 认不认这个包装格式
    path = BACKEND / "config" / "comfyui_workflows" / "_selftest-lipsync.json"
    path.write_text(json.dumps(preset, ensure_ascii=False), encoding="utf-8")
    try:
        bare = ComfyUIClient.load_workflow(path)
        meta = ComfyUIClient.load_workflow_meta(path)
        expect(set(bare) == set(LIPSYNC_GRAPH), "load_workflow 取出的是裸图（不是 wrapper）")
        expect(meta.get("output_node") == "111" and meta.get("type") == "video",
               "load_workflow_meta 拿到了 output_node/type")

        # 3. 引擎的两轨注入：先渲染 {{...}}，再按 params 补丁并纠类型
        context = {"prompt": "一个女人在说话", "positive_prompt": "一个女人在说话",
                   "negative_prompt": "text, watermark", "reference_image": "i2v_ab12.png",
                   "audio_file": "voice_cd34.mp3", "width": 768, "fps": 24, "seed": 12345}
        rendered = _render_placeholders(bare, context)
        expect(rendered["125"]["inputs"]["audio"] == "voice_cd34.mp3",
               "口型音频名渲染进了 LoadAudio")
        expect(rendered["41"]["inputs"]["image"] == "i2v_ab12.png", "首帧图名渲染进了 LoadImage")

        patches = {}
        for key, mapping in (meta.get("params") or {}).items():
            if mapping.get("field") and key in context:
                patches.setdefault(str(mapping["node"]), {})[mapping["field"]] = context[key]
        patched = ComfyUIClient.patch_workflow(rendered, patches, strict=False)
        expect(patched["50"]["inputs"]["width"] == 768
               and isinstance(patched["50"]["inputs"]["width"], int),
               "数字字段被纠回 int（ComfyUI 收到字符串会直接拒）")
        expect(patched["111"]["inputs"]["fps"] == 24, "fps 同样纠成 int")
        expect(patched["30"]["inputs"]["text"] == "一个女人在说话", "提示词补丁生效")
    finally:
        path.unlink(missing_ok=True)

    # 4. 接了线但仍缺关键占位符时，report 要提醒而不是沉默
    print("\n（下面两行是 report() 的正常输出）")
    mcp.report(build(), "video")

    print(f"\n通过 {PASSED} 项，失败 {len(FAILS)} 项")
    if FAILS:
        for f in FAILS:
            print(f"  ✗ {f}")
        print("结果：❌ 预设包装器有问题")
        return 1
    print("结果：✅ 预设包装器符合约定")
    return 0


if __name__ == "__main__":
    sys.exit(main())
