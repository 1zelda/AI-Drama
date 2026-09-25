"""把一个 ComfyUI 的 API 导出文件包装成本项目的视频/图片预设（含占位符接线）。

用法：
    cd ai-drama-studio/backend
    python scripts/make_comfyui_preset.py <你的导出.json> --name lipsync-infinite-talk \
        --type video --output-node 111 \
        --map prompt=93.text --map reference_image=97.image --map audio_file=125.audio \
        [--description "InfiniteTalk 口型同步"] [--force] [--print-only]

为什么要这个脚本：官方示例（含 InfiniteTalk / VACE / V2V 那些）都是 ComfyUI 的
UI 存档格式，带坐标、连线编号、SetNode/GetNode，引擎吃不了；引擎要的是 API 格式 +
一个 params 映射表。在 ComfyUI 里用「Workflow → Export (API)」导一次，剩下的接线
交给本脚本，比手改 500 行 JSON 靠谱得多。

--map 的写法是 占位符名=节点号.字段名。脚本会：
  1. 校验该节点和字段真的存在（写错节点号是最常见的哑火原因）；
  2. 把那个字段的值换成 {{占位符名}}，跑图时由引擎填进去；
  3. 在预设的 params 里记下它在哪 —— 数字型字段（width/length/fps）靠这条路生效。

跑完之后：把生成的预设文件名填进工作流节点的 workflow_file，
比如 realistic-drama.json 里默认关闭的 lipsync 节点。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PRESET_DIR = BACKEND / "config" / "comfyui_workflows"
# 引擎在每次跑图时一定会提供的上下文键，见 providers/video_providers.py
SUPPLIED = ("prompt", "positive_prompt", "negative_prompt", "length", "duration", "seed",
            "width", "height", "fps", "reference_image", "audio_file")


def die(msg: str) -> "None":
    print(f"✗ {msg}")
    sys.exit(1)


def parse_map(spec: str) -> tuple[str, str, str]:
    if "=" not in spec:
        die(f"--map 要写成 占位符=节点号.字段名，收到的是 {spec!r}")
    key, target = spec.split("=", 1)
    if "." not in target:
        die(f"--map {spec!r} 缺少字段名，应形如 reference_image=97.image")
    node_id, field = target.rsplit(".", 1)
    return key.strip(), node_id.strip(), field.strip()


def build_preset(graph: dict, *, name: str, wtype: str, output_node: str,
                 mappings: list, description: str) -> dict:
    """把 API 图里的指定字段换成占位符，并生成 params 映射表。"""
    if not isinstance(graph, dict) or not graph:
        die("导出的文件是空的或不像 API 格式（应为 {节点号: {class_type, inputs}}）")
    if isinstance(graph.get("nodes"), list):
        die("这是 ComfyUI 的 UI 存档格式（有 nodes 数组和连线编号），引擎不能直接用。"
            "请在 ComfyUI 里 Workflow → Export (API) 重新导出。")

    params: dict = {}
    for key, node_id, field in mappings:
        node = graph.get(node_id)
        if not isinstance(node, dict):
            die(f"--map {key}={node_id}.{field}：图里没有节点 {node_id}。"
                f"现有节点号示例：{sorted(graph)[:8]}")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or field not in inputs:
            fields = sorted(inputs) if isinstance(inputs, dict) else []
            die(f"--map {key}={node_id}.{field}：节点 {node_id}"
                f"（{node.get('class_type')}）没有字段 {field}。可用字段：{fields}")
        linked = isinstance(inputs[field], list)
        if linked:
            print(f"  · 注意：{node_id}.{field} 原本接的是上游节点，换成占位符会断开那条连线")
        inputs[field] = "{{%s}}" % key
        params[key] = {"node": node_id, "field": field}

    if output_node and output_node not in graph:
        die(f"--output-node {output_node} 不在图里。现有节点号示例：{sorted(graph)[:8]}")
    return {
        "name": name,
        "description": description or f"由 make_comfyui_preset.py 从 API 导出包装而成",
        "type": wtype,
        "output_node": output_node or None,
        "nodes": graph,
        "params": params,
    }


def report(preset: dict, wtype: str) -> None:
    from app.providers.comfyui import ComfyUIClient

    found = {str(p.get("name")) for p in ComfyUIClient.scan_placeholders(preset["nodes"])}
    mapped = set(preset["params"])
    print(f"\n占位符：已接线 {sorted(mapped) or '无'}")
    unwired = {k for k in found if "." not in k and k not in mapped}
    if unwired:
        print(f"  · 图里出现了 {sorted(unwired)}，但没有对应的 --map，跑图时会原样留在 JSON 里")
    missing = [k for k in SUPPLIED if k in found and k not in mapped]
    if missing:
        print(f"  · 引擎会提供但没接线的键：{missing}（不接就不生效）")
    if wtype == "video" and "audio_file" not in mapped:
        print("  · 没接 audio_file：这个预设做不了口型同步（ lipsync 节点会给它配音音频）")
    print("  · 模型权重是否装好，跑图前的 preflight 会检查；也可以先启动 ComfyUI 再试跑一镜")


def main() -> int:
    ap = argparse.ArgumentParser(description="把 ComfyUI API 导出包装成本项目的预设")
    ap.add_argument("source", help="ComfyUI「Export (API)」得到的 json 文件")
    ap.add_argument("--name", required=True, help="预设文件名（不带 .json）")
    ap.add_argument("--type", choices=["video", "image"], default="video")
    ap.add_argument("--output-node", default="", help="产出节点号，如 111")
    ap.add_argument("--map", action="append", default=[], dest="maps",
                    help="占位符=节点号.字段名，可重复")
    ap.add_argument("--description", default="")
    ap.add_argument("--force", action="store_true", help="允许覆盖同名预设")
    ap.add_argument("--print-only", action="store_true", help="只打印不写盘")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        die(f"找不到导出文件：{src}")
    try:
        graph = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"导出文件不是合法 JSON：{exc}")

    mappings = [parse_map(s) for s in args.maps]
    preset = build_preset(graph, name=args.name, wtype=args.type,
                          output_node=args.output_node, mappings=mappings,
                          description=args.description)
    report(preset, args.type)

    if args.print_only:
        print(json.dumps(preset, ensure_ascii=False, indent=2)[:4000])
        return 0
    PRESET_DIR.mkdir(parents=True, exist_ok=True)
    out = PRESET_DIR / f"{args.name}.json"
    if out.exists() and not args.force:
        die(f"{out.name} 已存在，要覆盖加 --force")
    out.write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 已写入 {out.relative_to(BACKEND)}")
    print(f"下一步：把工作流节点的 workflow_file 改成 \"{args.name}.json\"")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND))
    sys.exit(main())
