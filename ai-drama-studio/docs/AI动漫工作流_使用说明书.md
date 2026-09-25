# AI 动漫（漫剧）工作流 · 使用说明书

> 适用工作流：`anime-drama`（`ai-drama-studio/backend/config/workflows/anime-drama.json`）
> 版本：1.0.0 ｜ 编写日期：2026-09-24
> 四条线的公共部分（节点类型全表、通道选择、密钥存放、自检清单）在 [WORKFLOW_GUIDE](WORKFLOW_GUIDE.md)，本手册只讲这条线特有的。

这条线把「一句创意」变成一条**画风统一的竖屏动漫成片**。它和真人短剧线（`drama-pro`）
跑的是同一套引擎、同一批节点类型，区别全在提示词与结构约束上：动漫线多了一道
**画风圣经**、两道**资产出图**（定妆图 / 背景板）、一道**尾帧**，并且每个镜头的模型
是**按镜头类型逐镜路由**的，不是整条线写死一个。

---

## 1. 先看流程图

```
一句创意（title / genre / style / shots / duration）
   │
   ├─① script_planner        llm    anime_script.yaml      → 剧本：梗概+节拍+名场面+视觉意向
   ├─② style_bible           llm    anime_style.yaml       → 画风圣经：style_token + negative_prompt   ← 全剧只决定这一次
   ├─③ character_generator   llm    anime_characters.yaml  → 人设：形象锚点 + 定妆图排版指令
   ├─④ character_assets      image  foreach 角色            → 定妆图（给人审阅选优）
   ├─⑤ scene_generator       llm    anime_scenes.yaml      → 场景设定 + 空镜提示词 + 标志物
   ├─⑥ scene_assets          image  foreach 场景            → 背景板（无人、无字）
   ├─⑦ storyboard_generator  llm    anime_storyboard.yaml  → 分镜：首帧/尾帧/运动 三套提示词
   ├─⑧ shot_images           image  foreach 镜头 + 质检      → 每一镜的首帧（带角色参考图）
   ├─⑨ shot_end_frames       image  foreach 镜头 【出厂关闭】 → 每一镜的尾帧（首尾帧用）
   ├─⑩ narration             tts    foreach 镜头            → 逐镜配音（字幕同源）
   ├─⑪ shot_videos           video  foreach 镜头            → 图生视频
   └─⑫ final_cut             postprocess                   → 归一化+烧字幕+垫配音+片头片尾 → final.mp4
```

产物落盘位置：

| 内容 | 路径 |
|---|---|
| 图片 / 视频 / 音频 | `backend/output/anime-drama/<节点id>/` |
| 一次运行的状态与断点 | `backend/data/runs/<run_id>.json` + 同名目录 |
| 工作流定义 | `backend/config/workflows/anime-drama.json` |
| 六个动漫提示词模板 | `backend/config/prompts/scripts/anime_*.yaml` |

---

## 2. 三个设计要点（理解这三条，其余参数都是细节）

### ① 画风圣经：全剧只决定一次风格

`style_bible` 节点从一张固定表里挑一种画风基底（赛璐璐动画帧 / 厚涂漫画 / 平涂漫画 /
国风条漫 / 日系二次元漫剧 / 复古港漫 / 水彩叙事 / 极简叙事漫画），产出一段英文
`style_token`（40-70 词）和一段 `negative_prompt`。

之后 **④⑥⑧⑨ 四个生图节点的提示词开头都是同一句 `{{style_bible.style_token}}`**，
负面词也同源。分镜模板里明确禁止分镜师自己再写画风词或画质词——
两个风格源打架是「十张图像十部番」的头号原因。

想换风格：只改 `style_bible` 一个节点（或者在提示词模板里改那张表），
其余节点不用动。想直接跳过 LLM 定风格，把 `style_bible` 的产物手改成固定 JSON 也行。

### ② 角色一致性靠三层，越往下越强

| 层 | 谁负责 | 生效方式 | 强度 |
|---|---|---|---|
| 文字锚点 | `anime_characters.yaml` 的 `image_prompt` | 分镜逐字照抄 + 引擎 `_apply_character_refs` 再补一次 | 基础，零成本 |
| 定妆图 | `character_assets` 节点 | 出图给人看：风格串能不能用、角色脸立不立得住，在这里定 | 人工选优 |
| 参考图 | 角色档案库 `backend/data/<project>/characters/` | `character_refs: true` 时按镜头里的角色名自动挂进 `reference_images` | 最强 |

**注意第三层的现实**：引擎只从「角色档案库」读参考图。现在有了自动回填能力
（真人剧线的 ④ 就挂着它），但**动漫线的 `character_assets` 出厂没开**，因为开了之后
每一镜会改用你这次的定妆照当参考图，画风跨度大时反而会把角色带跑。想吃到最强的一致性：

> 给 anime-drama 的 character_assets 加上 register_character，定妆图自动入库

加上 `register_character: true` 与 `register_character_max: 2` 即可，细则见
`真人剧工作流_使用说明书.md` §3（含"手动上传的参考图不会被冲掉"这条规则）。

### ③ 动漫的夸张靠镜头和符号，不靠人体大动作

`anime_storyboard.yaml` 的第 1、2、3 条铁律：一个镜头只给一个运动动词；
禁止转身/奔跑/跳跃/打斗接触；激烈感改用

- 机位：`slow zoom in` / `push in` / `tilt up` / `pan left` / `tracking shot` / `orbit`
- 漫画符号：速度线、集中线、闪白帧、背景网点飞散、镜头抖动
- 切镜节奏：静镜 → 突然特写

这不是审美偏好，是当前图生视频模型的硬约束：写「少年挥拳冲上去」基本必融化。

---

## 3. 逐镜模型路由（`{{shot_type}}`）

⑧⑨⑪ 三个节点的 `shot_type` 写的是 `"{{shot_type}}"`，也就是**由分镜自己逐镜填**：

| 分镜填的值 | 生图首选 | 生视频首选 | 禁用 |
|---|---|---|---|
| `character_closeup` 人物特写 | zhipu | zhipu | **agnes** |
| `character_action` 全身动作 | zhipu | zhipu | **agnes** |
| `scene` 空镜 | agnes | agnes | — |
| `screen_ui` 屏幕/道具 | 本地 Ken Burns | 同左 | agnes |
| `fx_transition` 特效转场 | agnes | agnes | — |

规则表在 `backend/config/shot_routing.json`，改它不用动工作流：
设置页「镜头路由」或直接 `PUT /api/routing`。

两个必须知道的后果：

1. **人物镜头禁用 agnes**（真人脸和动漫脸都算，理由是人脸保持差 + 带角标）。
   如果你机器上只配了 `AGNES_API_KEY`，人物镜头会明确报错并告诉你该配 zhipu，
   不会悄悄出废片。想放开就在路由表里把 `agnes` 从 `banned` 里删掉。
2. 分镜没填或填了不认识的 `shot_type` 时，引擎退回默认通道，不会中断。

---

## 4. 首尾帧（`shot_end_frames`，出厂关闭）

动漫最缺的不是画，是「这一镜从什么姿势变到什么姿势」。首尾帧把这件事变成两张图：

- 分镜模板对每一镜同时产出 `image_prompt`（首帧）和 `end_frame_prompt`（尾帧）。
  尾帧要求**自带全部锚点**、同构图、只改那一处变化——写成"同上只改 X"是无效提示词。
- ⑪ `shot_videos` 上挂了 `first_frame_from: shot_images` + `last_frame_from: shot_end_frames`。
  两张图都取到且都有效时，引擎自动用 `mode: "first_last_frame"`；否则退回 `first_frame`。

为什么出厂 `disabled: true`：

- 尾帧是**整条线最贵的一刀**——分镜图数量直接翻倍；
- 只有 `seedance`（走 base64，不需要公网）和 `agnes`（需要 `PUBLIC_ASSET_BASE_URL`）
  认首尾帧，`zhipu` / `modelscope` / `kling` / `comfyui` 会**静默忽略尾帧**，
  等于白花钱。

**怎么打开**：流水线页的参数框填不出布尔值（这是刻意的，文本框里的 `false` 也是真值），
所以用 AI 助手说一句就行：

> 把 anime-drama 的 shot_end_frames 节点打开，我要用 seedance 跑首尾帧

或者在 `anime-drama.json` 里把 `"disabled": true` 整行删掉。

---

## 5. 怎么跑

### A. 一键成片页（推荐，改动最少）

`/make` 页面顶部的「① 用哪条流水线」选 **AI 动漫（漫剧）**，之后的表单和真人线一样：
创意 → 画幅 → 镜头数 → 画风（动漫线给的是赛璐璐 / 3D 国漫 / 国风条漫 / 二次元漫剧 /
复古港漫 / 水彩叙事六种，直接对齐画风圣经那张表）。

「先出 2 张候选关键帧」这个开关在动漫线上尤其值钱——它给 `shot_images`、
`shot_end_frames` 都挂上 `preview_gate: 2`，先花两镜的钱验画风，确认后
点「断点续跑」把剩下的跑完。

### B. 流水线画布

`/pipeline` 选 `anime-drama`，可以逐节点看参数、改参数、看产物，右上角「运行」跑的是
**磁盘上的版本**（画布上没保存的改动不会生效，先点保存）。

### C. API

> 端口按你实际起的改：默认 8000，而本仓库 `frontend/.env.local` 把后端指到了 8013。

```bash
curl -X POST http://127.0.0.1:8000/api/runs/ -H "Content-Type: application/json" -d '{
  "workflow": "anime-drama",
  "input": {"title":"少年在毕业礼上被判定零天赋，当晚体内的封印之瞳睁开",
            "genre":"热血异能","style":"日漫赛璐璐，硬边色块，均匀描线，高对比",
            "shots":6,"duration":"30 秒","episodes":1},
  "overrides": {"shot_images": {"preview_gate": 2}}
}'
```

`input` 里的键就是模板里的 `{{title}} {{genre}} {{style}} {{shots}} {{duration}}`。
注意：**只有这些是扁平可引用的**，上游节点的字段必须写成 `{{节点id.字段}}`
（例 `{{script_planner.synopsis}}`），而且那个节点 id 要在 `depends_on` 里。
漏挂依赖不会报错，只会在提示词里留下原样的大括号，LLM 就开始自由发挥。

---

## 6. 用 AI 聊天改这条线

面板在 `/pipeline` 的「AI 助手」页签。所有改动都是**提案 → 你勾选 → 应用**，
不会偷偷写盘（细则见 `AI助手改步骤_使用说明书.md`）。下面这些句式实测在契约内：

| 你说 | 它会做 |
|---|---|
| 这个画风太素了，改成高对比的复古港漫风 | patch `anime_style` 模板里那张画风基底表 / 或给 `style_bible` 加约束 |
| 分镜里别再写"转身""奔跑"，全部换成机位运动 | `prompt.patch`：改 `anime_storyboard` 第 2 条铁律并给出新正文 |
| 每一镜改 8 秒 | `workflow.node.patch` ⑪ `shot_videos.seconds`，并把 `final_cut` 相关一起看 |
| 打开尾帧，我要跑首尾帧 | patch ⑨ `disabled` → 删除该字段 |
| 同框人物太多了，限制成 1 个 | ⑧ `character_refs_max` → 1，并提示分镜模板第 5 条一起改 |
| 分镜图先只出 2 张给我看 | ⑧ `preview_gate` → 2 |
| 质检太严了，老是重生成 | ⑧ `qc.threshold` 6 → 7 或 `max_attempts` → 1 |
| 给我加一个「漫画格分割」节点，放在分镜图之后 | `workflow.node.add`（新 image 节点，depends_on 与 foreach 一并配好） |
| 把 ⑥ 背景板删掉 | `workflow.node.remove`，会先告诉你下游哪几条边会被顺带改动 |
| 新建一条「条漫风」工作流，基于 anime-drama 改 | `workflow.create` |

---

## 7. 成本开关速查

| 开关 | 位置 | 默认 | 说明 |
|---|---|---|---|
| `preview_gate` | ⑧ / ⑨ | 无（一键成片页给 2） | 先出 N 张就停下等确认，最省钱的机制 |
| `qc.max_attempts` | ⑧ | 2 | 不及格最多重生几张；`threshold` 6 分及格 |
| `character_refs_max` | ⑧⑨ | 2 | 同框参考图上限，>2 一致性掉得很快 |
| `concurrency` | ④⑥⑧⑨⑩ | 2 | 云端生图并发；尾帧链/上一镜续接会自动降成 1 |
| `disabled` | ⑨ | true | 尾帧整节点关闭 |
| 角色 ≤3 / 场景 ≤3 / 每镜 5 秒 | 三个 llm 模板的铁律里 | — | 动漫按张算钱，这是模板层面兜住的 |

---

## 8. 排错

| 症状 | 原因 | 怎么办 |
|---|---|---|
| 报错「镜头类型 'character_closeup' 禁用 provider 'agnes'」 | 只有 Agnes 的 Key，而人物镜头禁用了它 | 配 `ZHIPU_API_KEY`；或在 `shot_routing.json` 里放开 agnes（知道会掉脸稳） |
| 打开尾帧后没效果 | 当前视频通道不认首尾帧 | `shot_videos.provider` 设 `seedance`（免公网）或 `agnes`（需 `PUBLIC_ASSET_BASE_URL`） |
| 每张图风格都不一样 | 有人在分镜里自己写了画风/画质词 | 让 AI 助手把 ⑦ 模板第 6 条收紧；确认只有 `style_bible` 产出风格词 |
| 提示词里出现原样的 `{{xxx}}` | 引用了非直接上游的字段 | 把那个节点挂进 `depends_on`，或改成 `{{节点id.字段}}` 写法 |
| 定妆图和分镜里的人不像同一个 | 只用了文字锚点 | 让 AI 助手给 ④ 加 `register_character`（见 §2 第三层） |
| 角色说话全是一个声音 | 动漫线分镜没有 `speaker` 列 | 引擎已支持分角色音色：让 AI 助手给 ⑦ 模板加 `speaker` 输出、给 ⑩ 配 `voices_from`/`speaker_field`；细节见 `真人剧工作流_使用说明书.md` §4 |
| 画面融成一团 | 一镜写了多个动作 | 看 ⑪ 的 `prompt` 实际值，回模板收紧「一片段一动词」 |
| 成片有模糊边 | 生图尺寸与成片画幅不一致 | 一键成片页会自动同步；手动跑时 ⑧⑨⑥ 的 width/height 要跟 `final_cut` 同比例 |

---

## 9. 自检

改完工作流或模板，先跑这两条（不联网、不花 token、不动生产文件）：

```bash
cd ai-drama-studio/backend
python scripts/test_workflows.py     # 出厂四条工作流 + 全部模板的连通性：201 项
python scripts/test_engine_frames.py # 首尾帧取帧与 mode 推导：13 项
python scripts/test_engine_voice.py  # 分角色音色、定妆图入库、口型音频配对：16 项
python scripts/test_copilot.py       # AI 助手提案→校验→落盘：34 项
```

`test_workflows.py` 查的是那些"跑起来才发现"的错：模板文件不存在、
`{{节点id.字段}}` 的头节点没挂进 `depends_on`、`foreach` 来源不是直接上游
（会静默退化成只跑 1 项）、首帧/尾帧/配音/字幕来源指向不存在的节点、以及引擎自己的拓扑排序。

---

## 10. 与真人短剧线（drama-pro）的差异一览

| | 真人短剧 `drama-pro` | AI 动漫 `anime-drama` |
|---|---|---|
| 节点数 | 8 | 12（多画风圣经、定妆图、背景板、尾帧） |
| 画风来源 | `plot.yaml` 里的 `visual_style`，靠分镜自觉照抄 | 独立节点 `style_bible`，四个生图节点强制拼接 |
| 镜头类型 | 节点上写死 `character_action` | 逐镜 `{{shot_type}}` 路由 |
| 质检模板 | `qc_vision.yaml`（怕油画/水墨/3D 味） | `anime_qc_vision.yaml`（怕真人质感/3D 渲染/厚涂糊） |
| 首尾帧 | 无 | `shot_end_frames` + `last_frame_from`（默认关） |
| 夸张度 | 微表情、写实表演 | 机位 + 漫画符号 + 切镜节奏 |
| 片尾卡 | 未完待续 | 下集预告（`{{script_planner.ova_teaser}}`） |

## 11. 已知限制（诚实清单）

1. **定妆图默认不回灌成参考图**（`character_assets` 出厂没挂 `register_character`），
   跨镜一致性目前主要靠文字锚点；要吃到最强那一层见 §2 第三层。
2. **单音色配音**：动漫线一个 voice 念所有台词。引擎的分角色音色能力已经就绪，
   缺的是 ⑦ 分镜模板输出 `speaker` 列 + ⑩ 节点配 `voices_from`/`speaker_field`，
   一句话就能补（见 §8 排错表最后一行）。
3. 引擎仍没有「把上一镜的成品图当构图参考」的逐镜引用，`prev_frame_refs` 只取
   紧邻上一项的一张图。
4. 首尾帧目前只有 `seedance` / `agnes` 真正生效，其他通道静默忽略（引擎不会替你拒绝，
   别指望报错提醒你）。
5. 尾帧节点出厂关闭，布尔值只能在 AI 助手里改或手改 JSON，画布上没有开关。
