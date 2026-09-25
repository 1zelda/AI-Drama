# AI Drama Studio · AI 短剧 / 动漫 / 片段魔改工作台

一句创意（或一条现成的视频）进去，成片出来。中间每一步都能用自然语言改。

四条出厂流水线：

| 线 | 工作流 id | 输入 | 一句话 |
| --- | --- | --- | --- |
| 真人短剧（快） | `drama-pro` | 一句创意 | 八段链路，先出片再说 |
| 真人剧（写实电影感） | `realistic-drama` | 一句创意 | 摄影规格 + 定妆照入档案库 + 分角色配音 + 口型同步 |
| AI 动漫（漫剧） | `anime-drama` | 一句创意 | 画风圣经只算一次，逐镜按镜头类型换模型 |
| 片段魔改（换世界观） | `video-restyle` | **一条现成视频** + 目标风格 | 「假如龙珠是印度人拍的」：构图不动，换掉一切可替换物 |

## 快速开始

```bash
# 1) 后端（默认 8000；本仓库 frontend/.env.local 指向 8013，按你的实际端口跑）
cd backend
python -m venv venv && venv/Scripts/activate        # mac/linux: source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 2) 前端（另开终端）
cd ../frontend && npm install && npm run dev        # http://localhost:3000
```

必装：**ffmpeg + ffprobe** 在 PATH 里（或 `.env` 写 `FFMPEG_BIN` / `FFPROBE_BIN`）。
没有它只能出图，出不了片。

可选：**ComfyUI**（本机有 GPU 时才值得装，`python main.py --listen` → `http://localhost:8188`）。
它是唯一能做口型同步（InfiniteTalk）和整段视频编辑（VACE）的路子，但预设要自己导出。

然后打开 `http://localhost:3000/settings`，**填一个智谱 API Key** 就够起步
（GLM-4-Flash 写剧本 + CogView-Flash 生图 + CogVideoX-Flash 生视频，官方都免费），
点保存 → 点「一键自检」→ 去 `/make` 选一条线开跑。

Key 也可以直接写在根目录 `.env`（照 `.env.example` 抄，含真实 Key，别提交）。
设置页保存的值会自动同步进后端进程的环境变量，不用重启；手改 `.env` 需要重启才读进去。

## 文档

- **[docs/WORKFLOW_GUIDE.md](docs/WORKFLOW_GUIDE.md)** ← 先看这份：四条线怎么选、13 种节点类型、通道怎么挑、怎么用 curl 跑
- [docs/AI助手改步骤_使用说明书.md](docs/AI助手改步骤_使用说明书.md) — 用自然语言改某一步（能改哪些字段、安全边界）
- [docs/AI动漫工作流_使用说明书.md](docs/AI动漫工作流_使用说明书.md)
- [docs/真人剧工作流_使用说明书.md](docs/真人剧工作流_使用说明书.md)
- [docs/片段魔改工作流_使用说明书.md](docs/片段魔改工作流_使用说明书.md)

## 页面

| 路径 | 干什么 |
| --- | --- |
| `/make` | 一键成片：选线 → 填参数 → 跑，跑完直接看成片 |
| `/pipeline` | 画布：看/改节点、逐项编辑、右侧 AI 助手能一句话改工作流和提示词 |
| `/workflows` | 工作流列表与 JSON 编辑 |
| `/routing` | 逐镜路由：每种镜头类型用哪个模型、禁哪个、运动模板、去水印 |
| `/history` | 成片库（跑完自动入库，可回放/删除） |
| `/assets` | 素材库（上传源视频、参考图；魔改线的入口） |
| `/audio` | 配音与克隆音色管理 |
| `/project/[id]` | 老的项目工作台（对话 + 审批板 + 角色档案） |

## 出厂自检（离线、不联网、不花钱）

```bash
cd backend
python scripts/test_workflows.py       # 工作流 ↔ 提示词模板 ↔ 依赖引用是否连通
python scripts/test_video_input.py     # 读入视频 / 抽帧 / 关键帧配镜 / 整段编辑 / BGM
python scripts/test_settings_env.py    # 设置页 Key → 环境变量 → 通道挑选
python scripts/test_comfyui_preset.py  # 本地 ComfyUI 预设包装器
python scripts/test_engine_frames.py   # 首尾帧按下标配对
python scripts/test_engine_voice.py    # 分角色配音 / 定妆图入库 / 口型音频
python scripts/test_copilot.py         # AI 助手提案的校验与写回
```

全绿只代表结构与约定没问题；出片质量要真跑一条才知道（先用节点的 `preview_gate` 只出 1～2 镜）。

## 目录

```
ai-drama-studio/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI 路由（runs / copilot / system / assets / …）
│   │   ├── orchestrator/   # DAG 引擎：拓扑、foreach、重试、质检、断点续跑
│   │   ├── providers/      # 生图 / 生视频 / ComfyUI / 公网素材发布
│   │   ├── services/       # 工作流与模板存储、通道自动挑选、后期合成、配音
│   │   └── main.py
│   ├── config/
│   │   ├── workflows/           # 四条线 + standard-drama 的节点图 JSON
│   │   ├── prompts/scripts/     # 21 份提示词模板（剧情约定在这）
│   │   ├── comfyui_workflows/   # 本地 ComfyUI 预设
│   │   ├── shot_routing.json    # 逐镜路由表
│   │   └── restyle_presets.json # 22 条「假如这条片子是 ___ 拍的」
│   ├── data/               # 项目 / run 状态 / 素材 / 音色
│   ├── output/             # 产物：<工作流>/<节点>/…，成片在 <工作流>/<filename>
│   └── scripts/test_*.py   # 出厂自检
├── frontend/               # Next.js（App Router）+ ReactFlow
├── docs/                   # 说明书，见上
└── .env.example
```

## 主要接口

| 方法 · 路径 | 干什么 |
| --- | --- |
| `POST /api/runs/` | 起一条运行：`{workflow, input, overrides}`（overrides 按节点 id 浅合并，只活这一次） |
| `GET /api/runs/{id}/events` | SSE 进度流（每个节点每次重试的中文状态） |
| `POST /api/runs/{id}/resume` | 断点续跑 / 过 `preview_gate` 这道人工门 |
| `GET /api/runs/meta/node-types` | 引擎认识的节点类型（前端下拉的唯一来源） |
| `POST /api/copilot/chat` → `/apply` | 自然语言改工作流 JSON 与提示词模板（提案 → 确认 → 落盘） |
| `GET /api/system/health` | 一键自检；`GET /api/system/restyle_presets` 列目标风格 |
| `POST /api/assets/upload` | 传源视频 / 参考图 |

## License

MIT
