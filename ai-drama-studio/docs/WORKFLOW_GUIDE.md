# 四条流水线总览 · 使用说明书

这份文档管**横向**的东西：有哪些工作流、13 种节点类型分别吃什么字段、
通道（供应商）怎么挑、怎么跑、跑坏了怎么自检。
每条线自己的剧情约定、模板铁律、排错细节，看下面这份分工表指向的那本。

| 想知道 | 看哪份 |
| --- | --- |
| 有哪些线、节点类型、通道、怎么跑、怎么自检 | 本文 |
| 用一句自然语言改某一步（含 AI 助手能改哪些字段） | [AI助手改步骤_使用说明书.md](AI助手改步骤_使用说明书.md) |
| 动漫（漫剧）线的画风圣经 / 逐镜路由 / 首尾帧 | [AI动漫工作流_使用说明书.md](AI动漫工作流_使用说明书.md) |
| 真人剧线的摄影规格 / 定妆照入库 / 分角色配音 / 口型同步 | [真人剧工作流_使用说明书.md](真人剧工作流_使用说明书.md) |
| 片段魔改线的反推 / 换世界观 / 整段编辑 / 风格库 | [片段魔改工作流_使用说明书.md](片段魔改工作流_使用说明书.md) |
| 当初为什么这么搭（编排引擎 vs ComfyUI） | [ARCHITECTURE.md](ARCHITECTURE.md)（历史文档，部分描述已过时） |

---

## 1. 先跑通一次（十分钟）

```bash
# 后端（默认 8000；本仓库 frontend/.env.local 把 NEXT_PUBLIC_API_BASE 指到了 8013，
# 所以照 .env.local 跑的话，下面所有 8000 都换成 8013）
cd ai-drama-studio/backend
python -m venv venv && venv/Scripts/activate      # Windows；mac/linux 用 source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（另开一个终端）
cd ai-drama-studio/frontend
npm install && npm run dev                        # http://localhost:3000
```

然后按这个顺序，**别跳步**：

1. 装 **ffmpeg + ffprobe** 并放进 PATH（或者在 `.env` 里写 `FFMPEG_BIN` / `FFPROBE_BIN` 绝对路径）。
   抽帧、合成成片、烧字幕、去角标全靠它，没装只能出图不能出片。
2. 打开 `http://localhost:3000/settings`，**只填一个智谱 API Key** 就够起步
   （GLM-4-Flash 写剧本、CogView-Flash 生图、CogVideoX-Flash 生视频，官方都免费），点保存。
3. 同一页点「一键自检」。它逐项报 LLM / 生图 / 生视频 / 配音 / FFmpeg / 字幕字体 / ComfyUI /
   魔改线 的就绪情况，缺什么会点名要配什么。前四项齐了才算能出片。
4. 去 `http://localhost:3000/make`，选一条线，写一句创意，点生成。
5. 成片在 `backend/output/<工作流名>/<filename>`（例如 `output/drama-pro/final.mp4`），
   历史页 `/history` 里能直接回放，中间产物在同一个工作流目录下的节点子目录里。

> **Key 存在哪**：设置页保存写 `frontend/data/settings.json`；生图/生视频/配音通道读的是
> 环境变量。后端读到设置后会把它同步进进程的环境变量（`.env` 里手填的值优先，不会被界面盖掉），
> 所以**改完设置不用重启**也生效。反过来，手改 `.env` 需要重启后端才读进去
> （`load_dotenv(override=False)` 只在启动时灌一次）。
> `backend/../.env` 含真实 Key，已 gitignore，别提交、别截图。

---

## 2. 四条线怎么选

| | 真人短剧（快） | 真人剧（写实电影感） | AI 动漫（漫剧） | 片段魔改（换世界观） |
| --- | --- | --- | --- | --- |
| 工作流 id | `drama-pro` | `realistic-drama` | `anime-drama` | `video-restyle` |
| 输入 | 一句创意 | 一句创意 | 一句创意 | **一条现成的视频** + 一个目标风格 |
| 节点数 | 8 | 13 | 12 | 7 |
| 一致性靠什么 | 角色/场景设定复用 | 摄影规格 + 定妆照自动入档案库 + 逐镜参考图 | 画风圣经 + 定妆图 + `{{shot_type}}` 逐镜选模型 | 原片关键帧当参考图重绘（构图尽量不动） |
| 台词 | 旁白配音 | 分角色配音（可上口型同步） | 旁白配音 | **保留原片音轨**，不重配 |
| 出厂关闭的节点 | — | `shot_end_frames`、`lipsync` | `shot_end_frames` | `video_edit`（整段编辑，需百炼 Key） |
| 什么时候用 | 先验证剧本、赶时间 | 真人实拍质感、多人对话 | 二次元/条漫/国漫 | 「假如龙珠是印度人拍的」这类整片改写 |

还有一条 **`standard-drama`**（分集 + `ffmpeg` 拼接的老七段流水线）不在 `/make` 上，
只在 `/pipeline` 画布里能跑，适合想手动改拓扑的人；它是唯一 `parallel: true` 的出厂线。

页面对应关系：`/make` 是「填参数就开跑」，`/pipeline` 是「看画布、逐项改、能和 AI 助手说话」。
同一条线在两边跑的是同一份 JSON，`/make` 只是把常用参数覆盖上去（见 §6）。

---

## 3. 工作流 JSON 长什么样

`backend/config/workflows/<name>.json`：

```jsonc
{
  "name": "anime-drama",
  "description": "…跑这条线要知道的事都写在这里…",
  "version": 1,
  "execution": { "parallel": false, "retry": 2, "timeout": 900,
                 "concurrency": 1, "heartbeat_interval": 10 },
  "nodes": {
    "script_planner": {
      "type": "llm",
      "provider": "deepseek", "model": "deepseek-chat",
      "prompt_template": "anime_script.yaml",
      "output": "script"
    },
    "shot_images": {
      "type": "image",
      "depends_on": ["storyboard_generator", "character_generator", "style_bible"],
      "foreach": "storyboard.shots",
      "width": 768, "height": 1344,
      "qc": { "enabled": true, "threshold": 6, "max_attempts": 2,
              "prompt_template": "anime_qc_vision.yaml" },
      "disabled": false
    }
  }
}
```

- `nodes` 的**键就是节点 id**，下游用 `{{节点id.字段}}` 引用它，`depends_on` 决定拓扑顺序。
- 只有 `foreach` 的来源写「别名.字段」（`output` 存在的唯一理由），提示词里引用一律用节点 id。
- 一条边只有挂进 `depends_on` 才会进渲染上下文 —— 漏挂的结果是提示词里留下原样的 `{{...}}` 大括号。
- `disabled: true` 是整个跳过该节点，下游拿不到它的产物时会自动降级（不改结构）。
  出厂那几个关闭的节点都是「路线开关」，不是残缺功能。
- 运行状态落在 `backend/data/runs/<run_id>.json`（每个节点的状态和输出都在里面），
  foreach 的每一镜另存 `data/runs/<run_id>/shots/<节点id>/<序号>.json` 供断点续跑；
  节点自己的图/视频/音频产物在 `output/<工作流名>/<节点id>/`。跑废了先看这两处，比看日志快。

---

## 4. 13 种节点类型（权威表）

引擎的分发表就是这份，前端「添加节点」下拉从 `GET /api/runs/meta/node-types` 读它：

`llm` `text` `image` `comfyui_image` `comfyui` `video` `comfyui_video` `agnes_video`
`tts` `video_input` `ffmpeg` `postprocess` `noop`

所有类型共用的字段：`depends_on`、`foreach`、`output`、`retry` / `timeout` / `concurrency`、
`disabled`、`preview_gate`（跑完 N 项停下等人工确认的省钱门）、`qc`（生图质检）、`approval_before`。

| type | 干什么 | 关键专有字段 | 产物 |
| --- | --- | --- | --- |
| `llm` | 写剧本 / 人设 / 分镜 | **`prompt_template`（必填，正文不在 JSON 里）**、`model`、`json_mode`、`images_from`（挂 `video_input` 则走视觉模型看关键帧，最多 4 张）、`max_tokens` | 解析后的 JSON（按字段引用） |
| `text` | 引擎里和 `llm` 是同一个处理器（别名），语义上表示「产出一段文本」 | 同 `llm` | 同上 |
| `image` | 生图（定妆图 / 分镜首帧） | `provider`、`prompt`、`negative_prompt`、`width/height`、`character_refs`、**`init_image_from`**（把上游第 i 张图配给第 i 镜当被改写对象）、`register_character`（定妆图入档案库）、`shot_type` | `paths`/`images` |
| `comfyui_image` / `comfyui` | 生图族的别名（引擎里和 `image` 同一个处理器），跑本地 ComfyUI 图预设 | `workflow_file` + `comfy_url` + `output_node` | `paths` |
| `video` | 图生视频（每镜一段） | `provider`、`mode`、`first_frame_from`、`last_frame_from`、**`source_video_from`**（整段编辑的源片）、`audio_from`（口型）、`seconds`、`resolution`、`tail_frame_from_prev` | `paths`/`videos` |
| `comfyui_video` | 走 `video` 同一个处理器，只是 provider 固定本地 ComfyUI | 同 `video` | `paths` |
| `agnes_video` | **老专用通道**，独立实现（历史兼容）。新连线请用 `video` + `provider: "agnes"` | 少一半字段：不吃 `source_video_from` 等新接线 | `paths` |
| `tts` | 配音 | `engine`(`edge`/`gptsovits`)、`voice`、`speaker_field`、`voices_from`、`voice_map`、`voice_pool`、`rate`/`pitch` | `paths`/`audios` |
| `video_input` | **读一条现成视频**（不生产内容） | `source`、`frames`(≤12)、`frame_times`、`max_side`、`extract_audio`、`clip_seconds` | `videos`/`frames`/`audios` + `duration`… **故意没有 `paths`** |
| `ffmpeg` | 单步音视频处理 | `from`、`filename`、`reencode` | `paths` |
| `postprocess` | 合成成片 | `from`、`width/height/fps/crf/fit`、`burn_subtitle` + `subtitles_from`、`bgm`/`bgm_from`/`bgm_loop`、`title_card`/`end_card`、`logo`/`trim_badge`、`crossfade` | `paths`（成片） |
| `noop` | 占位/汇聚 | 无 | — |

两个反复踩的坑，单拎出来：

1. **`video_input` 没有 `paths`**。所以引用它时必须按用途选对字段：图用 `init_image_from` /
   `images_from`，音频用 `bgm_from` / `voices_from`。写成 `from: ["input_clip"]` 取不到东西。
2. **打开一个默认关闭的生产节点，必须同时改 `final_cut.from`**。
   比如把 `video_edit` 打开、却没把 `final_cut` 的 `from` 从 `["shot_videos"]` 改成 `["video_edit"]`，
   成片里还是旧内容，而你会以为那个节点没生效。

完整字段清单以 `backend/app/api/copilot.py` 里的 `FIELD_REFERENCE` 为准 ——
AI 助手就是拿它当说明书用的，那份是最新的。

---

## 5. 通道（供应商）怎么挑

引擎在每个 `image` / `video` 节点执行前调 `provider_picker.pick()`：
**节点上写了 `provider` 就用它**（没配 Key 就顺延到下一个可用的并在进度里说明为什么换）；
没写就按「免费优先 + 已配置优先」自动挑。

- 生图顺序：`zhipu → modelscope → siliconflow → pollinations → http → comfyui`
- 生视频顺序：`zhipu → modelscope → seedance → agnes → kling → comfyui → dashscope`
- `comfyui` 只有你在设置页点过「Check Connection」通过后才算可用（避免默认 URL 就把它当通道）。
- `dashscope` 排在最后：它是付费通道，自动挑时永远不抢免费位的活，
  但节点写死 `provider: "dashscope"` 时能正常用上（`video_edit` 路线就靠它）。

| 通道 | 需要 | 支持的模式 | 备注 |
| --- | --- | --- | --- |
| 智谱 | `ZHIPU_API_KEY` | text / image | 官方免费，一个 Key 同时覆盖 LLM+图+视频，**起步推荐** |
| 魔搭 | `MODELSCOPE_TOKEN` | text / image | 每日 2000 次共享额度，体验级 |
| 硅基流动 | `SILICONFLOW_API_KEY` | 生图 | 免费层 Kolors，URL 一小时过期已自动下载 |
| Pollinations | `POLLINATIONS_KEY` | 生图 | 2026 起需 Key |
| Seedance（火山方舟） | `ARK_API_KEY` | text / first_frame / first_last_frame / reference | **参考图走 base64，不用开公网隧道**；认首尾帧 |
| Agnes | `AGNES_API_KEY` | text / keyframe / reference | 2.5 Flash 限时免费；**吃图必须要 `PUBLIC_ASSET_BASE_URL`**；真人脸镜头被路由表禁用 |
| 可灵 | `KLING_API_KEY` | text / image | 付费 |
| 阿里百炼 | `DASHSCOPE_API_KEY` | text / first_frame / first_last_frame / **video_edit** | 图片走 data URI 不用隧道；`wan2.7-videoedit` 整段编辑需要**源视频的公网 URL** |
| ComfyUI | 本机服务 `http://localhost:8188` | 本地图/视频 | 唯一能做口型同步（InfiniteTalk）和整段编辑（VACE）的路子，但要自己导预设 |

**「公网素材地址」`PUBLIC_ASSET_BASE_URL` 是什么**：云端供应商只能自己下载文件，看不到你硬盘上的
`output/xxx.png`。后端会把要给它看的文件复制到 `backend/static/assets/` 并拼成
`<PUBLIC_ASSET_BASE_URL>/static/assets/<名字>`，所以需要一个指向后端端口的内网穿透
（后端在 8000 就 `ngrok http 8000`，在 8013 就 `ngrok http 8013`；
或 `cloudflared tunnel --url http://localhost:8000`）。
只有「云端通道 + 要吃本地图/视频」的组合才需要它；纯文生视频、Seedance、智谱都不需要。

逐镜选模型由 `backend/config/shot_routing.json` 决定（`shot_type` → preferred / fallback /
banned / 运动模板 / 去水印预设），页面在 `/routing`，也可以直接 `PUT /api/routing`。
真人脸镜头禁用 agnes、场景与特效优先 agnes、屏幕 UI 走本地 Ken Burns 都写在这份表里。

---

## 6. 一条命令跑起来（不打开浏览器）

```bash
# 端口按你实际起的改（.env.local 指向 8013 时把 8000 全换掉）
curl -s http://127.0.0.1:8000/api/runs/meta/node-types      # 引擎认哪些节点类型
curl -s http://127.0.0.1:8000/api/system/health | python -m json.tool   # 一键自检
curl -s http://127.0.0.1:8000/api/system/restyle_presets    # 魔改线的 22 个目标风格

# 起一条运行。input 的键就是模板里的 {{...}} 占位符：
# 四条生成线都认 title / genre / duration / style / shots / episodes / voice，
# 魔改线额外认 source_video（素材 id 或本机路径）+ 摊平后的 preset_* 八个键。
curl -s -X POST http://127.0.0.1:8000/api/runs/ -H "Content-Type: application/json" -d '{
  "workflow": "anime-drama",
  "input": {
    "title": "少年剑客在废弃空间站里找到最后一株植物",
    "genre": "科幻", "duration": "30 秒", "style": "", "shots": 6
  },
  "overrides": {
    "final_cut":   { "width": 1080, "height": 1920 },
    "narration":   { "voice": "zh-CN-XiaoxiaoNeural" },
    "shot_images": { "preview_gate": 2 }
  }
}'
```

- `overrides` 是**按节点 id 浅合并**到那份 JSON 上，只为这一次运行生效、不写回文件。
  后端会拒绝写了不存在的节点 id（HTTP 400），所以别凭记忆写。
- 跟进度：`GET /api/runs/{run_id}/events`（SSE 事件流，含每个节点每次重试的中文状态）。
- 停在 `preview_gate` 上：状态是 `waiting_approval`，人工确认后 `POST /api/runs/{run_id}/resume`。
- 产物清单：`GET /api/runs/{run_id}/artifacts`。
- 后端重启后内存里没有这条 run 了也没关系，状态从 `backend/data/runs/` 恢复，能接着 resume。

---

## 7. 每一步都能用一句话改

`/pipeline` 右侧的 AI 助手面板（后端 `POST /api/copilot/chat` → 确认后 `POST /api/copilot/apply`）
能改两类东西：

1. **工作流 JSON**：改节点参数、加/删节点、调 `depends_on`、开关 `disabled`；
2. **提示词模板** `backend/config/prompts/scripts/*.yaml`：改正文、加字段、改铁律。

安全边界（为什么可以放心让它写文件）：单轮最多 12 条提案；动作必须在白名单里、
节点必须真的存在、`type` 必须是引擎认识的那 13 种之一、`depends_on` 不能悬空也不能成环
（落盘时还要再过一遍引擎自己的拓扑校验）；每条 patch 带 `before` 值，界面上逐条给你看 diff
才落盘。它改的是磁盘上的工作流 JSON / 模板 yaml（可回滚靠 git），不碰本次运行的 `overrides`。

它**不校验**字段名拼错这种问题 —— 引擎对不认识的字段是「看不见就当没有」，
所以确认前扫一眼字段名是不是 §4 表里那些（AI 助手拿得到同一份字段清单，但它也会拼错）。

常用说法（更多见各线说明书的「用 AI 聊天改这一步」）：

| 你说 | 效果 |
| --- | --- |
| 「把成片改成横屏」 | `final_cut.width/height` 改 1920×1080 |
| 「第 3 步多加一句：不要出现现代物品」 | 改对应 `llm` 节点的 yaml 模板 |
| 「配音换成温柔点的女声」 | `narration.voice` / `voice_pool` |
| 「先只跑 2 个镜头让我看看」 | `shot_images.preview_gate = 2` |
| 「质检太严了，放宽松一点」 | `qc.threshold` 调低或 `max_attempts` 减小 |
| 「这一镜不要用 Agnes，脸会崩」 | 该节点 `provider` 或路由表的 `banned` |
| 「把魔改线的风格换成 80 年代港片」 | 这条走 `/make` 的下拉，或直接改 `preset_*` 输入 |

---

## 8. 出厂自检（离线、不联网、不花钱）

改过工作流 JSON、模板、引擎或设置链路之后，跑这一遍：

```bash
cd ai-drama-studio/backend
python scripts/test_workflows.py        # 243 项：拓扑能排序、模板真存在、{{引用}} 的上游确实挂了依赖、来源指向真实节点、音色格式
python scripts/test_video_input.py      #  51 项：读入视频 / 抽帧 / 关键帧配镜 / 整段编辑源片 / bgm 循环
python scripts/test_settings_env.py     #  25 项：设置页 Key → 环境变量 → 通道挑选；.env 合并不会毁掉手填值
python scripts/test_comfyui_preset.py   #  14 项：本地 ComfyUI 预设包装器
python scripts/test_engine_frames.py    #  13 项：首尾帧按下标配对
python scripts/test_engine_voice.py     #  16 项：分角色配音、定妆图入库、口型音频
python scripts/test_copilot.py          # 全通过：AI 助手的提案校验与写回
```

七份脚本都是「跑不到网络、花不到钱」的约定测试，`test_agnes.py` 需要真实 Key 才能跑，不在这份清单里。
全部 ✅ 只代表**结构与约定**没问题；实际出片质量只能真跑一条验证。

---

## 9. 加一条自己的工作流（或者加一种新节点）

**只想换口味**：`/pipeline` 里改现有线 → 或复制 `config/workflows/anime-drama.json`
改个 `name` 存成新文件（工作流列表是每次请求现扫目录的，**不用重启**就出现在
`GET /api/runs/workflows` 和 `/pipeline` 的下拉里；`/make` 那四条线是写死在页面里的，
要上新线得在 `frontend/app/make/page.tsx` 的 `WORKFLOWS` 加一项 + 一份阶段清单。
`_` 开头的文件会被忽略，所以别拿 `_mini-test.json` 当模板）。
然后照 §8 跑 `test_workflows.py`：它会挑出引用了不存在的模板、`{{...}}` 指到了没挂 `depends_on`
的节点、`foreach` / 首帧 / 配音 / 字幕来源指向不存在的节点、以及音色 id 写错这类问题。
字段名拼错它不管（见 §7 最后一段）。

**要加一种节点类型**：在 `app/orchestrator/engine.py` 的分发表里注册一个 `_execute_xxx`，
并把类型名加进 `app/services/workflow_store.py` 的 `NODE_TYPES`（前端下拉从后端读它，不用改两份）。
新字段务必同步三处，否则 AI 助手改不到、说明书会骗人：
`app/api/copilot.py` 的 `FIELD_REFERENCE`、指向别的节点的那类字段要加进
`scripts/test_workflows.py` 的 `iter_dep_specs`（不然校验脚本查不到它），以及对应说明书。

**要加一个供应商**：在 `app/providers/video_providers.py`（或 `image_providers.py`）里
继承基类实现 `generate()`，注册进 `PROVIDERS` 和 `list_*_providers()`；
如果要让用户在设置页填 Key，还得在 `app/services/provider_picker.py` 的
`_KEY_TO_ENV` / `_REQUIREMENT` / 偏好顺序里各加一行（`test_settings_env.py` 会盯着这条链）。

---

## 10. 已知限制（诚实清单）

- **出厂工作流没有在真机上跑通过**：开发机缺 ffmpeg 与 `python-multipart`（上传接口起不来），
  所以 §8 全是离线约定测试。第一次跑请先用 `preview_gate` 只出 1～2 镜。
- `backend/venv` 是在别的机器上建的（指向不存在的 `C:\Python313`），**不能用**，请重建。
- 魔改线「镜头数 = 抽帧数」，`vision_completion` 一次最多看 4 张图，想一镜一帧地精细反推需要改代码。
- 魔改线没有台词层：字幕、对白重配都不做，只把原片音轨垫回成片（`bgm_from`，且默认不循环）。
- `video_edit`（整段换风格、运动不变）与 `lipsync`（口型同步）两条路线**都还没被真实验证过**：
  前者需要百炼 Key + 公网地址，后者需要你自己从 ComfyUI 导出 InfiniteTalk 预设
  （`scripts/make_comfyui_preset.py` 教你怎么导）。
- `/pipeline` 的「运行」按钮跑的是**磁盘上已保存的**工作流，画布上没保存的改动不会生效。
- 根目录的 `启动.bat` **别用**：它里面硬编码了一个 **Agnes API Key**（应当作废并删掉这一段）、
  引用的都是别人机器上的绝对路径和外部项目目录，而且只起后端不起前端 —— 打开 8013 看到的是
  FastAPI 的接口，不是界面。按 §1 那两条命令起才对。
- 提示词模板里的中文铁律靠模型自觉，构图/一致性都不是硬锁 —— 质检门只打分、不保证。
