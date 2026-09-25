#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单集生成结果 → 剪映草稿（pyJianYingDraft 适配层）

把 $short-drama-produce 产出的镜头片段组装成可直接用剪映打开的多轨草稿：
视频轨（含转场）+ 字幕轨（SRT）+ BGM 轨（自动淡入淡出）。

依赖安装：
    pip install pyJianYingDraft        # 本地包: _upgrade/pyJianYingDraft
    （自动导出 MP4 需本机装有剪映 ≤6，Windows）

清单文件格式（单集清单.json）：
{
  "width": 1080, "height": 1920,
  "shots": [
    {"file": "output/EP01/s01.mp4", "transition": "叠化", "subtitle": "台词或旁白"},
    {"file": "output/EP01/s02.mp4", "transition": "", "subtitle": ""}
  ],
  "bgm": "output/EP01/bgm.mp3",          // 可选
  "bgm_volume": 0.4,
  "srt": "output/EP01/EP01.srt"          // 可选；与 shots[].subtitle 二选一或并用
}

transition 取剪映转场中文名（pyJianYingDraft.TransitionType 属性名，如 叠化/信号故障/闪黑），
留空则硬切。

用法：
    python export_jianying_draft.py 单集清单.json --draft-root "<剪映草稿目录>" --name "EP01"
    python export_jianying_draft.py 单集清单.json --draft-root auto --name "EP01" --export
    （--draft-root auto 自动探测默认剪映草稿目录；--export 调起剪映自动导出成片）
"""
import argparse
import json
import os
import re
import sys

import pyJianYingDraft as draft
from pyJianYingDraft import trange, Timerange


def detect_draft_root() -> str:
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft"),
        os.path.expandvars(r"%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft"),
        os.path.join(os.getcwd(), "jianying_drafts"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[-1]


def parse_srt(path: str):
    """极简 SRT 解析 → [(start_sec, end_sec, text), ...]"""
    out = []
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        m = re.match(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1]
        )
        if not m and len(lines) > 2:
            m = re.match(
                r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[0]
            )
            lines = lines[1:] if m else lines
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = "\n".join(lines[2:])
        out.append((start, end, text))
    return out


def build(manifest_path: str, draft_root: str, name: str, do_export: bool, fps: int = 30):
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    width = manifest.get("width", 1080)
    height = manifest.get("height", 1920)
    shots = manifest["shots"]
    for s in shots:
        if not os.path.isfile(s["file"]):
            sys.exit(f"镜头文件不存在: {s['file']}")

    folder = draft.DraftFolder(draft_root)
    script = folder.create_draft(name, width, height, allow_replace=True, fps=fps)

    script.append_tracks([
        draft.TrackSpec(draft.TrackType.audio, "bgm"),
        draft.TrackSpec(draft.TrackType.video, "main"),
        draft.TrackSpec(draft.TrackType.text, "caption"),
    ])

    cursor_us = 0
    prev_seg = None
    for i, s in enumerate(shots):
        mat = draft.VideoMaterial(s["file"])
        dur_us = mat.duration
        seg = draft.VideoSegment(mat, Timerange(cursor_us, dur_us))
        script.add_segment(seg, "main")
        if prev_seg is not None and s.get("transition"):
            ttype = getattr(draft.TransitionType, s["transition"], None)
            if ttype is not None:
                prev_seg.add_transition(ttype)
            else:
                print(f"[warn] 未知转场「{s['transition']}」，第 {i} 镜硬切")
        if s.get("subtitle"):
            script.add_segment(
                draft.TextSegment(
                    s["subtitle"], seg.target_timerange,
                    style=draft.TextStyle(color=(1.0, 1.0, 1.0)),
                    clip_settings=draft.ClipSettings(transform_y=-0.75),
                ),
                "caption",
            )
        cursor_us += dur_us
        prev_seg = seg

    total_us = cursor_us

    if manifest.get("srt") and os.path.isfile(manifest["srt"]):
        for start, end, text in parse_srt(manifest["srt"]):
            if start >= total_us / 1e6:
                continue
            script.add_segment(
                draft.TextSegment(
                    text, trange(f"{start}s", f"{max(end - start, 0.5)}s"),
                    style=draft.TextStyle(color=(1.0, 1.0, 1.0)),
                    clip_settings=draft.ClipSettings(transform_y=-0.75),
                ),
                "caption",
            )

    if manifest.get("bgm"):
        if not os.path.isfile(manifest["bgm"]):
            print(f"[warn] BGM 文件不存在，跳过: {manifest['bgm']}")
        else:
            bgm = draft.AudioSegment(
                manifest["bgm"], trange("0s", f"{total_us / 1e6}s"),
                volume=manifest.get("bgm_volume", 0.4),
            )
            bgm.add_fade("1.5s", "2s")
            script.add_segment(bgm, "bgm")

    script.save()
    print(f"[ok] 草稿已写入: {os.path.join(draft_root, name)}")

    if do_export:
        from pyJianYingDraft import ExportFramerate, ExportResolution, JianyingController
        ctrl = JianyingController(draft_root)
        ctrl.export_draft(name, os.path.abspath(f"{name}.mp4"),
                          ExportResolution.resolution_1080p, ExportFramerate.fps_30)
        print(f"[ok] 已调起剪映导出: {name}.mp4")


def main():
    ap = argparse.ArgumentParser(description="单集清单 → 剪映草稿")
    ap.add_argument("manifest", help="单集清单.json 路径")
    ap.add_argument("--name", required=True, help="草稿名，如 我的新剧EP01")
    ap.add_argument("--draft-root", default="auto", help="剪映草稿根目录，auto 自动探测")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--export", action="store_true", help="写完草稿后调起剪映自动导出 MP4")
    args = ap.parse_args()

    root = detect_draft_root() if args.draft_root == "auto" else args.draft_root
    if not os.path.isdir(root):
        os.makedirs(root, exist_ok=True)
        print(f"[info] 草稿目录不存在，已创建: {root}")
    build(args.manifest, root, args.name, args.export, args.fps)


if __name__ == "__main__":
    main()
