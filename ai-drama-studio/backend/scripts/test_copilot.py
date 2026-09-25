"""AI 助手写回链路的离线自检（不联网、不花 token、不动生产工作流）。

用法：
    cd ai-drama-studio/backend
    python scripts/test_copilot.py

openai / python-dotenv 缺失时自动打桩，LLM 回复用固定 JSON 假扮，
这样在没有可用解释器的机器上也能验证「提案 → 校验 → 落盘」这段真正会改文件的代码。
"""

from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


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
    _stub("openai", AsyncOpenAI=AsyncOpenAI)

try:
    import dotenv  # noqa: F401
except ImportError:
    _stub("dotenv", load_dotenv=lambda *a, **kw: False)

from app import llm  # noqa: E402
from app.api import copilot  # noqa: E402
from app.services import prompt_store, workflow_store  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

WF = "_selftest-copilot"
NEW_WF = "_selftest-new-flow"
TPL = "_selftest_tpl"

FAILS: list = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def make_client():
    app = FastAPI()
    app.include_router(copilot.router)
    return TestClient(app)


def seeded_workflow():
    return {
        "name": WF,
        "description": "自检用",
        "nodes": {
            "script": {"type": "llm", "prompt_template": "plot.yaml", "depends_on": []},
            "shots": {"type": "image", "depends_on": ["script"], "foreach": "story.shots",
                      "output": "images", "width": 720, "height": 1280},
            "videos": {"type": "video", "depends_on": ["shots"], "seconds": 5},
        },
    }


def cleanup():
    for name in (WF, WF + "_x", WF + "_y", NEW_WF):
        try:
            workflow_store.delete(name)
        except Exception:  # noqa: BLE001
            pass
    try:
        prompt_store.delete(TPL)
    except Exception:  # noqa: BLE001
        pass


def main():
    cleanup()
    client = make_client()
    try:
        # ---------- 1. workflow_store ----------
        print("\n[1] workflow_store 读写与校验")
        workflow_store.write(WF, seeded_workflow(), create=True)
        check(WF not in workflow_store.list_workflows(), "下划线开头的自检文件不进 UI 列表")
        check(workflow_store.read(WF)["nodes"]["videos"]["seconds"] == 5, "读回内容一致")

        bad = seeded_workflow()
        bad["nodes"]["script"]["depends_on"] = ["videos"]  # 成环
        try:
            workflow_store.write(WF + "_x", bad)
            check(False, "循环依赖应被拒绝")
        except workflow_store.StoreError as e:
            check("循环" in str(e) or "cycle" in str(e).lower(), f"循环依赖被拒绝：{e}")

        bad2 = seeded_workflow()
        bad2["nodes"]["shots"]["type"] = "imagegen"
        try:
            workflow_store.write(WF + "_y", bad2)
            check(False, "未知节点类型应被拒绝")
        except workflow_store.StoreError as e:
            check("imagegen" in str(e), f"未知节点类型被拒绝：{e}")

        check(not (BACKEND / "config" / "workflows" / "_selftest-copilot_x.json").exists(),
              "校验失败的写入不会留下文件")

        # ---------- 2. prompt_store ----------
        print("\n[2] prompt_store 模板读写")
        keys = [t["key"] for t in prompt_store.list_templates()]
        check("plot" in keys, f"列出内置模板：{keys}")
        plot = prompt_store.read("plot")
        check("{{title}}" in plot["prompt"], "plot.yaml 带 {{title}} 占位符")
        used = prompt_store._used_by("plot.yaml")
        check(any(u.startswith("drama-pro") for u in used), f"反查到引用方：{used}")
        prompt_store.write(TPL, {"prompt": "你好 {{title}}", "config": {"model": "x"}}, create=True)
        try:
            prompt_store.write(TPL, {"prompt": "没有占位符"})
            check(True, "无占位符模板允许保存（只是普通模板）")
        except prompt_store.StoreError as e:
            check(False, f"不该拒绝：{e}")
        # 模板正文里带 JSON 示例是常态，花括号不该被当成坏占位符
        try:
            prompt_store.write(TPL, {"prompt": '输出 {"title":"xxx"}，主题 {{title}}'})
            check(True, "含 JSON 示例的模板正常保存")
        except prompt_store.StoreError as e:
            check(False, f"不该拒绝：{e}")
        # 复制 plot.yaml（带中文注释头）后改写，注释头不能丢
        shutil.copyfile(BACKEND / "config" / "prompts" / "scripts" / "plot.yaml",
                        prompt_store.path_for(TPL))
        original = prompt_store.path_for(TPL).read_text(encoding="utf-8").splitlines()
        head0 = next(l for l in original if l.startswith("#"))
        doc_plot = prompt_store.read(TPL)
        doc_plot["prompt"] = doc_plot["prompt"] + "\n加一条：结尾留钩子。"
        prompt_store.write(TPL, doc_plot)
        saved = prompt_store.path_for(TPL).read_text(encoding="utf-8")
        check(head0 in saved and saved.index(head0) < saved.index("prompt:"),
              "改正文后模板的中文注释头保住了")
        check("结尾留钩子" in prompt_store.read(TPL)["prompt"], "正文改动生效且 YAML 仍可解析")
        try:
            prompt_store.write(TPL, {"config": {"model": "x"}})
            check(False, "缺 prompt 字段应被拒绝")
        except prompt_store.StoreError:
            check(True, "缺 prompt 字段被拒绝")
        try:
            prompt_store.delete("plot")
            check(False, "被节点引用的模板不该能删")
        except prompt_store.StoreError as e:
            check("引用" in str(e), f"引用保护生效：{e}")

        # ---------- 3. /chat 产出提案 ----------
        print("\n[3] POST /api/copilot/chat（假 LLM）")
        fake = {
            "reply": "改成横屏，并加一个配音节点；顺带把正文提示词的温度调低。",
            "proposals": [
                {"action": "workflow.node.patch", "workflow": WF, "node_id": "shots",
                 "patch": {"width": 1280, "height": 720}, "reason": "横屏 16:9"},
                {"action": "workflow.node.patch", "workflow": WF, "node_id": "nope",
                 "patch": {"width": 1}, "reason": "不存在的节点"},
                {"action": "prompt.patch", "key": TPL,
                 "patch": {"config": {"temperature": 0.4}}, "reason": "降温度"},
                {"action": "workflow.node.add", "workflow": WF,
                 "node": {"id": "voice", "type": "tts", "depends_on": ["shots"],
                          "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural"}, "reason": "加配音"},
                {"action": "shell.rm", "workflow": WF, "reason": "不支持的动作"},
            ],
        }
        async def fake_completion(**kw):
            prompt = json.dumps(kw.get("messages"), ensure_ascii=False)
            assert "workflow.node.patch" in prompt, "系统提示里应带输出契约"
            assert "shot_type" in prompt, "系统提示里应带字段说明"
            return "```json\n" + json.dumps(fake, ensure_ascii=False) + "\n```"
        llm.planner.chat_completion = fake_completion

        r = client.post("/api/copilot/chat", json={
            "message": "把分镜图改成横屏，加一个配音节点，温度降到 0.4",
            "workflow": WF, "node_id": "shots",
        })
        check(r.status_code == 200, f"chat 返回 200（实际 {r.status_code}）")
        body = r.json()
        check(body["reply"].startswith("改成横屏"), "reply 透传")
        check(len(body["proposals"]) == 3, f"合法提案 3 条（实际 {len(body['proposals'])}）")
        check(len(body["rejected"]) == 2, f"非法提案拦下 2 条（实际 {len(body['rejected'])}）")
        check(all(p.get("base_hash") for p in body["proposals"]), "提案都带上了 base_hash")
        check(body["proposals"][0]["before"] == {"width": 720, "height": 1280},
              "patch 带了改前快照，前端能画 diff")

        # ---------- 4. /apply 落盘 ----------
        print("\n[4] POST /api/copilot/apply")
        r = client.post("/api/copilot/apply", json={"proposals": body["proposals"]})
        res = r.json()
        check(res["ok"], f"三条全部应用成功：{res}")
        doc = workflow_store.read(WF)
        check(doc["nodes"]["shots"]["width"] == 1280 and doc["nodes"]["shots"]["height"] == 720,
              "shots 尺寸已改成横屏")
        check("voice" in doc["nodes"], "新节点 voice 已加入")
        check(prompt_store.read(TPL)["config"]["temperature"] == 0.4,
              "prompt.patch 深合并了 config")

        stale = [{"action": "workflow.node.patch", "workflow": WF, "node_id": "shots",
                  "base_hash": "deadbeef0000", "patch": {"width": 1}, "reason": "过期提案"}]
        res = client.post("/api/copilot/apply", json={"proposals": stale}).json()
        check(not res["applied"] and "改过" in res["failed"][0]["reason"],
              f"base_hash 不匹配的提案被拒：{res['failed'][0]['reason']}")
        check(workflow_store.read(WF)["nodes"]["shots"]["width"] == 1280, "被拒的提案没有落盘")

        # 删除节点：下游依赖自动摘掉
        rm = [{"action": "workflow.node.remove", "workflow": WF, "node_id": "shots",
               "base_hash": copilot._wf_hash(WF), "reason": "删掉生图"}]
        res = client.post("/api/copilot/apply", json={"proposals": rm}).json()
        check(res["ok"], "删除节点成功")
        doc = workflow_store.read(WF)
        check("shots" not in doc["nodes"], "节点已移除")
        check(doc["nodes"]["videos"]["depends_on"] == [], "下游 depends_on 自动摘干净")

        # 新建整条工作流
        create = [{"action": "workflow.create", "workflow": NEW_WF, "base_hash": "",
                   "doc": {"name": NEW_WF, "nodes": {"a": {"type": "noop"}}}, "reason": "新链路"}]
        res = client.post("/api/copilot/apply", json={"proposals": create}).json()
        check(res["ok"], "workflow.create 成功")
        check(workflow_store.read(NEW_WF)["nodes"]["a"]["type"] == "noop", "新工作流内容正确")
        check("drama-pro" in workflow_store.list_workflows(),
              f"正式工作流照常列出：{workflow_store.list_workflows()}")

        # 应用一条会让文档非法的提案（引擎校验兜底）
        poison = [{"action": "workflow.node.add", "workflow": NEW_WF,
                   "base_hash": copilot._wf_hash(NEW_WF),
                   "node": {"id": "b", "type": "llm", "depends_on": ["c"]}, "reason": "坏依赖"}]
        res = client.post("/api/copilot/apply", json={"proposals": poison}).json()
        check(not res["applied"], "depends_on 指向不存在的节点会被拦下")
        check("c" in res["failed"][0]["reason"], f"报错说清了是谁：{res['failed'][0]['reason']}")

        # ---------- 5. 真实 LLM 路径提示 ----------
        print("\n[5] 跳过真实 LLM 调用（离线自检）")

    finally:
        cleanup()
        for p in (BACKEND / "config" / "workflows").glob("_selftest*"):
            p.unlink(missing_ok=True)
        for p in (BACKEND / "config" / "prompts" / "scripts").glob("_selftest*"):
            p.unlink(missing_ok=True)

    print("\n" + ("全部通过 ✅" if not FAILS else f"失败 {len(FAILS)} 项 ❌\n" + "\n".join(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
