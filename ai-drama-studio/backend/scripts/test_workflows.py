"""出厂工作流与提示词模板的静态连通性自检（不联网、不花 token、不写任何文件）。

用法：
    cd ai-drama-studio/backend
    python scripts/test_workflows.py

检查的是「跑起来才发现的那类错」，全部在内存里判定：
  1. 工作流能被引擎自己拓扑排序（循环/悬空依赖/非法节点类型）；
  2. llm 节点与质检引用的提示词模板真的存在；
  3. 模板/节点配置里的 {{节点id.字段}} 点号引用，其节点确实在 depends_on 里
     —— 引擎只会把「直接上游」放进渲染上下文，漏挂依赖时提示词里会留下原样的大括号，
     LLM 就开始自由发挥，这种错最难在成片里看出来；
  4. foreach 的来源、首帧/尾帧/参考图/配音/字幕/口型音频的来源都指向真实上游；
  5. tts 节点写的 edge-tts 音色 id 格式像话（写错的音色不会报错，只会静默退回默认音色）；
  6. 每条提示词模板至少被一个工作流用到（孤儿模板只提示，不算失败）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Windows 控制台默认 GBK，打不出 ✅/✗ 会把一次正常的自检变成编码崩溃。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml  # noqa: E402

from app.services import prompt_store, workflow_store  # noqa: E402

PROMPT_DIR = BACKEND / "config" / "prompts" / "scripts"
PRESET_DIR = BACKEND / "config" / "comfyui_workflows"
DOT_REF = re.compile(r"\{\{\s*([A-Za-z_]\w*(?:\.\w+)+)\s*\}\}")
BARE_REF = re.compile(r"\{\{\s*([A-Za-z_]\w*)\s*\}\}")
EDGE_VOICE = re.compile(r"^[a-z]{2}-[A-Za-z]{2,}-[\w-]+Neural$")

failures: list[str] = []
warnings: list[str] = []
passed = 0


def check(ok: bool, label: str) -> None:
    global passed
    if ok:
        passed += 1
    else:
        failures.append(label)


def walk_strings(value, prefix: str = ""):
    """产出 (字段路径, 字符串) —— 数组下标写成 a.0.b，报错时能一眼定位。"""
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from walk_strings(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from walk_strings(v, f"{prefix}.{i}")


def iter_dep_specs(node: dict):
    """节点里所有「指向别的节点」的字段值。"""
    yield "depends_on", node.get("depends_on") or []
    for key in ("first_frame_from", "last_frame_from", "images_from", "prompt_from",
                "init_image_from", "source_video_from", "bgm_from", "voices_from",
                "from", "subtitles_from", "audio_from"):
        v = node.get(key)
        if v is not None:
            yield key, v if isinstance(v, list) else [v]


def main() -> int:
    names = [n for n in workflow_store.list_workflows()]
    check(bool(names), "至少有一个工作流")
    if not names:
        print("没有找到任何工作流"); return 1

    used_templates: set[str] = set()

    for name in names:
        try:
            doc = workflow_store.read(name)
            workflow_store.validate(name, doc)
            check(True, f"{name}: 引擎结构校验")
        except workflow_store.StoreError as exc:
            check(False, f"{name}: 结构校验失败 -> {exc}")
            continue

        nodes = doc.get("nodes") or {}
        aliases = {str(c["output"]): nid for nid, c in nodes.items() if c.get("output")}

        for nid, node in nodes.items():
            where = f"{name}/{nid}"
            deps = set(node.get("depends_on") or [])

            # 2. 模板存在
            for field, tpl in (("prompt_template", node.get("prompt_template")),
                               ("qc.prompt_template", (node.get("qc") or {}).get("prompt_template"))):
                if not tpl:
                    continue
                used_templates.add(str(tpl))
                check((PROMPT_DIR / str(tpl)).exists(), f"{where}: {field}={tpl} 模板存在")

            # 3. 点号引用的头节点必须是直接上游
            for path, text in walk_strings(node, nid):
                for ref in DOT_REF.findall(text):
                    head = ref.split(".")[0]
                    check(head in deps, f"{where}: {path} 引用 {{{{{ref}}}}}，但 {head} 不在 depends_on")

            # 4. 节点引用与 foreach 来源
            for field, values in iter_dep_specs(node):
                for v in values:
                    check(str(v) in nodes, f"{where}: {field}={v} 指向不存在的节点")
                    if field != "depends_on":
                        check(str(v) in deps, f"{where}: {field}={v} 未挂进 depends_on（上游产物拿不到）")
            spec = node.get("foreach")
            if spec:
                head = str(spec).split(".")[0]
                check(head in deps or aliases.get(head) in deps,
                      f"{where}: foreach={spec} 的来源不是直接上游（会静默退化成只跑 1 项）")
            # 4b. 音色 id 写错不会报错，只会静默退回默认音色（三个人一个嗓子）
            if (node.get("type") or "").lower() == "tts" and (node.get("engine") or "edge") == "edge":
                cands: list = []
                if node.get("voice"):
                    cands.append(("voice", node["voice"]))
                for i, v in enumerate(node.get("voice_pool") or []):
                    cands.append((f"voice_pool.{i}", v))
                for k, v in (node.get("voice_map") or {}).items():
                    cands.append((f"voice_map.{k}", v if isinstance(v, str) else (v or {}).get("voice")))
                for path, val in cands:
                    if val and not EDGE_VOICE.match(str(val)):
                        warnings.append(f"{where}: {path}={val} 不像 edge-tts 音色 id（应形如 zh-CN-XiaoxiaoNeural）")

            # 4c. comfyui 节点引用的预设文件必须真的存在
            wf_file = node.get("workflow_file")
            if wf_file:
                exists = (PRESET_DIR / str(wf_file)).exists()
                if exists:
                    check(True, f"{where}: comfyui 预设 {wf_file} 存在")
                elif node.get("disabled"):
                    warnings.append(f"{where}: comfyui 预设 {wf_file} 还不存在（该节点当前 disabled；"
                                    "打开前先用 scripts/make_comfyui_preset.py 从 ComfyUI 的 API 导出生成）")
                else:
                    check(False, f"{where}: comfyui 预设 {wf_file} 不存在，跑图会直接 FileNotFoundError")

            # foreach 项字段（{{image_prompt}} 这类裸引用）不判：
            # 它来自 LLM 输出结构，静态检查看不到。
            for field, text in walk_strings(node, nid):
                for ref in BARE_REF.findall(text):
                    if ref in aliases and ref not in deps:
                        warnings.append(f"{where}: {field} 用了输出别名 {{{{{ref}}}}}（引擎按节点 id 渲染，建议改写为 {{{{{aliases[ref]}.{ref}}}}}）")

    # 5. 孤儿模板
    for meta in prompt_store.list_templates():
        fn = f"{meta['key']}.yaml"
        if fn in used_templates or meta["key"].startswith("_"):
            continue
        warnings.append(f"模板 {fn} 没有被任何工作流引用")

    for line in warnings:
        print(f"  · {line}")
    for line in failures:
        print(f"  ✗ {line}")
    print(f"\n工作流：{', '.join(names)}")
    print(f"通过 {passed} 项，失败 {len(failures)} 项，提示 {len(warnings)} 条")
    if failures:
        print("结果：❌ 有需要修的地方")
        return 1
    print("结果：✅ 出厂工作流与模板全部连通")
    return 0


if __name__ == "__main__":
    sys.exit(main())
