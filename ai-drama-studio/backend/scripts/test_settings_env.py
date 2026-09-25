"""设置页 → 环境变量 → 供应商这条链路的离线自检。

用法：
    cd ai-drama-studio/backend
    python scripts/test_settings_env.py

背景（这三条约定都是修出来的，不是设计出来就这样的）：

  1. 生图/生视频/配音通道取 Key 一律读 ``os.getenv``，而设置页保存只写
     ``frontend/data/settings.json``。不桥接就会出现「自检说已配置、
     真跑起来报缺 Key」，所以 ``apply_settings_to_env`` 必须把设置页的值
     灌进环境变量，且**外部（.env / 系统）已有的值优先**，用户手改的 .env
     不能被界面悄悄盖掉。
  2. 保存 .env 是**合并**不是整份重写：注释、以及这份映射管不到的键
     （FFMPEG_BIN、VISION_* 手填值…）必须原地留着；这次请求里没出现的字段
     一个字符都不能动，否则前端少传一个键就清了别人家 Key。
  3. dashscope（整段视频编辑 wan2.7-videoedit）是付费通道，
     自动挑通道时永远排最后，但节点显式写 provider=dashscope 时要用得上。
"""
from __future__ import annotations

import asyncio
import json
import os
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

from app.api import settings as settings_api  # noqa: E402
from app.services import provider_picker as pp  # noqa: E402

FAILS: list = []
PASSED = 0


def expect(ok: bool, label: str) -> None:
    global PASSED
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if ok:
        PASSED += 1
    else:
        FAILS.append(label)


def _clear_env(*names: str) -> None:
    for n in names:
        os.environ.pop(n, None)
        pp._INJECTED.pop(n, None)


async def main() -> int:
    print("\n[1] 设置页的 Key 要能变成环境变量")
    _clear_env("DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "PUBLIC_ASSET_BASE_URL")
    touched = pp.apply_settings_to_env({"dashscope_api_key": "sk-from-ui",
                                        "public_asset_base_url": "https://t.example"})
    expect(os.getenv("DASHSCOPE_API_KEY") == "sk-from-ui", "设置页填的 DashScope Key 进了环境变量")
    expect(touched == ["DASHSCOPE_API_KEY", "PUBLIC_ASSET_BASE_URL"],
           "返回值只有环境变量名，不带 Key 本体")
    expect("sk-from-ui" not in json.dumps(touched), "返回体里没有泄露 Key")

    os.environ["ZHIPU_API_KEY"] = "external-wins"
    pp.apply_settings_to_env({"zhipu_api_key": "from-ui"})
    expect(os.getenv("ZHIPU_API_KEY") == "external-wins", ".env / 系统里已有的 Key 不被界面盖掉")

    _clear_env("ZHIPU_API_KEY")
    pp.apply_settings_to_env({"zhipu_api_key": "v1"})
    pp.apply_settings_to_env({"zhipu_api_key": "v2"})
    expect(os.getenv("ZHIPU_API_KEY") == "v2", "改 Key 后不用重启也能生效（自己写过的值可以更新）")

    pp.apply_settings_to_env({"zhipu_api_key": ""})
    expect(not os.getenv("ZHIPU_API_KEY"), "界面上清空 Key 会真的拔掉环境变量")
    _clear_env("DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "PUBLIC_ASSET_BASE_URL")

    print("\n[2] dashscope 通道：显式点得到，自动挑不抢免费位")
    expect(pp.VIDEO_PREFERENCE[-1] == "dashscope", "VIDEO_PREFERENCE 里 dashscope 排在最后")
    s_both = {"zhipu_api_key": "z", "dashscope_api_key": "d"}
    expect("dashscope" in pp.configured("video", s_both), "配了 Key 就算已配置")
    expect(pp.pick("video", None, s_both)["provider"] == "zhipu", "自动挑通道仍选免费的智谱")
    picked = pp.pick("video", "dashscope", s_both)
    expect(picked["provider"] == "dashscope" and picked["source"] == "explicit",
           "节点写死 provider=dashscope 时照用")
    only_free = pp.pick("video", "dashscope", {"zhipu_api_key": "z"})
    expect(only_free["provider"] == "zhipu" and only_free.get("fallback_from") == "dashscope",
           "没配 DashScope Key 时顺延到别的通道，并说明为什么换")
    expect(pp._KEY_TO_ENV.get("dashscope_api_key") == "DASHSCOPE_API_KEY",
           "设置页字段映射里有 dashscope_api_key → DASHSCOPE_API_KEY")

    print("\n[3] 保存设置：.env 是合并，不是整份重写")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env_path = tmp / ".env"
        env_path.write_text(
            "# 手写的注释要留着\n"
            "FFMPEG_BIN=D:/tools/ffmpeg.exe\n"
            "VISION_MODEL=glm-4v-flash\n"
            "DASHSCOPE_API_KEY=sk-manual\n"
            "ZHIPU_API_KEY=zk-manual\n",
            encoding="utf-8")
        real_env, real_settings = settings_api.ENV_PATH, settings_api.SETTINGS_PATH
        settings_api.ENV_PATH = env_path
        settings_api.SETTINGS_PATH = tmp / "frontend" / "data" / "settings.json"
        try:
            settings_api._save_settings({"modelscope_token": "ms-new"})
            text = env_path.read_text(encoding="utf-8")
            expect("# 手写的注释要留着" in text, "注释原样保留")
            expect("FFMPEG_BIN=D:/tools/ffmpeg.exe" in text, "映射管不到的键没被抹掉")
            expect("VISION_MODEL=glm-4v-flash" in text, "本次请求里没出现的字段一个字都不动")
            expect("DASHSCOPE_API_KEY=sk-manual" in text, "没传 dashscope 时不会把别人的 Key 清空")
            expect("MODELSCOPE_TOKEN=ms-new" in text, "传了的字段写进了 .env")
            expect(os.getenv("MODELSCOPE_TOKEN") == "ms-new", "正在跑的后端立刻用上新 Key")

            settings_api._save_settings({"modelscope_token": ""})
            expect("MODELSCOPE_TOKEN=" in env_path.read_text(encoding="utf-8"),
                   "界面上清空后 .env 里也跟着清空")
            expect(not os.getenv("MODELSCOPE_TOKEN"), "清空后环境变量也拔掉（不会拿旧 Key 继续跑）")

            got = settings_api._load_settings()
            expect("dashscope_api_key" in got and "public_asset_base_url" in got,
                   "GET 设置时补齐了新增字段的默认值（前端不会拿到 undefined）")
        finally:
            settings_api.ENV_PATH = real_env
            settings_api.SETTINGS_PATH = real_settings
            os.environ.pop("MODELSCOPE_TOKEN", None)

    print("\n[4] 自检里的魔改线专项")
    from app.api import system as system_api
    from app.llm import planner
    from app.providers import asset_host as ah

    # 这一节全部用桩：真机上的 .env / settings.json 里有什么 Key 不该影响结论
    real_vision, real_configured = planner.vision_config, pp.configured
    real_host = ah._host
    try:
        def _restyle(vision, *, dashscope: bool, tunnel: bool):
            planner.vision_config = lambda: vision
            pp.configured = lambda mod, s=None: (["zhipu", "dashscope"] if dashscope else ["zhipu"])
            ah._host = types.SimpleNamespace(configured=tunnel)
            return system_api._check_restyle()

        _clear_env("DASHSCOPE_API_KEY", "PUBLIC_ASSET_BASE_URL", "VISION_API_KEY", "VISION_MODEL")
        empty = json.dumps(_restyle(None, dashscope=False, tunnel=False), ensure_ascii=False)
        expect("反推原画面" in empty and "DASHSCOPE" in empty and "PUBLIC_ASSET_BASE_URL" in empty,
               "什么都没配时把三样缺项全点名")

        ready = json.dumps(_restyle({"model": "glm-4v-flash", "api_key": "k", "base_url": "b"},
                                    dashscope=True, tunnel=True), ensure_ascii=False)
        expect("就绪" in ready, "三样配齐后不再报缺项")

        suspicious = json.dumps(_restyle({"model": "deepseek-chat", "api_key": "k", "base_url": "b"},
                                         dashscope=True, tunnel=True), ensure_ascii=False)
        expect("不像多模态" in suspicious, "视觉模型名看着是纯文本模型时单独提醒")

        no_ds = json.dumps(_restyle({"model": "gpt-4o", "api_key": "k", "base_url": "b"},
                                    dashscope=False, tunnel=True), ensure_ascii=False)
        expect("出厂关闭" in no_ds, "没配 DashScope 只提示 video_edit 用不了，不拦逐镜重绘")
    finally:
        planner.vision_config = real_vision
        pp.configured = real_configured
        ah._host = real_host

    print(f"\n通过 {PASSED} 项，失败 {len(FAILS)} 项")
    if FAILS:
        for f in FAILS:
            print(f"  ✗ {f}")
        print("结果：❌ 设置页到供应商这条链有问题")
        return 1
    print("结果：✅ 设置页的 Key 能一路走到供应商，且不会毁掉手填的 .env")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
