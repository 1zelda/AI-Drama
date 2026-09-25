# AI 助手改步骤 — 使用与维护说明书

适用版本：ai-drama-studio v1.1.0+（2026-09-24 之后）
面向对象：想用一句话改流水线，而不想手点参数表单的人
公共部分（节点类型全表、通道选择、密钥存放、自检清单）见 [WORKFLOW_GUIDE](WORKFLOW_GUIDE.md)。

---

## 一、这是什么

流水线画布（`/pipeline`）右侧新增的 **「AI 助手」** 页。你说人话，它把工作流 JSON 和
提示词 yaml 的改动做成一张张**提案卡片**，你勾选后点「应用所选」才写盘。

它能改的东西：

| 提案动作 | 改的是什么 | 典型说法 |
|---|---|---|
| `workflow.node.patch` | 某个节点的参数（模型、提示词、尺寸、时长、并发、预览门…） | 「把生图换成 16:9」「视频改 8 秒」 |
| `workflow.node.add` | 新增一个节点 | 「在生图之后加一个配音节点」 |
| `workflow.node.remove` | 删除节点，下游依赖自动摘掉 | 「把配音那步去掉」 |
| `workflow.meta.patch` | 工作流的描述/版本/全局 execution | 「标成 v2，横屏版」 |
| `workflow.create` | 新建整条工作流 | 「照这条链路给我建一条纯横屏的」 |
| `prompt.patch` | `config/prompts/scripts/*.yaml` 里 llm 节点的正文提示词 | 「分镜模板太空，加上景别和运镜约束」 |
| `prompt.create` | 新建提示词模板 | 「给我建一个真人剧表演提示词模板」 |

它**不能**做的：执行流水线、删除正式工作流之外的任何文件、跑 shell、改 `.env` 或 Key。
动作类型是服务端白名单，模型编不出来的动作会被直接丢弃并在界面上告诉你原因。

---

## 二、怎么用（三步）

1. `/pipeline` 顶部选工作流 → 想聚焦某个节点就**先点它**（助手会把这个节点当重点）。
2. 输入诉求，回车。等它读完整条链路，返回说明 + 提案卡片。
3. 卡片上默认全选，勾掉不想要的 → **应用所选**。界面提示「已写入 N 条」，画布自动重新拉取。

要点：
- **改提示词正文**要去 `prompt.patch`，不是在节点上写 `prompt`。llm 节点只有
  `prompt_template` 指向 yaml，正文存在 yaml 里；助手已经知道这件事，但你自己手改时别搞错。
- **未保存的画布改动**会被应用提案覆盖，界面会先弹确认。
- 助手说「这次没有产生可落地的改动」= 它判断你只是在提问，答案在回复正文里。

### 常用句式（都验证过能被正确翻译）

```
把整条链路改成 16:9 横屏
在 shots 之后加一个 tts 节点，用女声 zh-CN-XiaoxiaoNeural
shot_videos 打开首尾帧续接，并加 preview_gate 3 让我先看三镜
分镜提示词太干，改成带景别/运镜/光线三段式的
这条流程哪里最烧钱？怎么省           ← 只会回答，不会改文件
```

---

## 三、安全性设计（为什么可以放心让它写文件）

1. **只提议，不偷写**。`POST /api/copilot/chat` 全程不碰磁盘，返回的是结构化提案。
2. **双重校验**。提案生成时校验一次（节点存不存在、类型合不合法、名称合不合法），
   应用时再校验一次（模型可能给出过期提案）。
3. **引擎级落盘前校验**。任何写入都会先写到临时文件、用引擎自己的
   `WorkflowConfig` 跑一遍拓扑排序，成环 / 悬空依赖 / 缺 `type` 直接拒绝，坏数据不会留在盘上。
4. **乐观并发**。每条提案带 `base_hash`（提案那一刻文件的 sha1 前 12 位）。
   应用时若磁盘已被别处改过 → 拒绝并提示「重新提问生成新提案」，不会静默覆盖。
5. **单轮上限 12 条**，超出的丢弃并告知。
6. **同批顺序应用**。一次提问的多条提案针对同一份文档时按顺序累积应用，
   彼此不会互相顶掉指纹。
7. **模板引用保护**。被节点引用的提示词模板删不掉（`prompt_store.delete` 会报引用方）。
8. **注释不丢**。写回 yaml 时会把原来挂在键前面的中文注释块重新贴回去
   （`prompt_store._reattach_comments`），否则改一次提示词就顺手删掉一份写作铁律。
9. 下划线开头的文件（`_xxx.json` / `_xxx.yaml`）不进 UI 列表，方便放自检与草稿。

---

## 四、后端接口一览

```
POST   /api/copilot/chat            {message, workflow, node_id, prompt_key, history}
                                   -> {reply, proposals[], rejected[]}
POST   /api/copilot/apply          {proposals[]}  -> {applied[], failed[], ok}

GET    /api/prompts/                模板清单（含 placeholders / used_by / 字数）
GET    /api/prompts/{key}           单个模板全文
PUT    /api/prompts/{key}           保存
POST   /api/prompts/{key}           新建
DELETE /api/prompts/{key}           删除（被引用则拒绝）

GET    /api/runs/workflows          工作流清单
GET    /api/runs/workflows/{name}   读
PUT    /api/runs/workflows/{name}   保存（画布用）
POST   /api/runs/workflows/{name}   新建（body 可空，给单节点骨架）
DELETE /api/runs/workflows/{name}   删除
GET    /api/runs/meta/node-types    引擎支持的节点类型（前端下拉的唯一来源）
```

代码位置：

- `backend/app/api/copilot.py` — 提案生成/校验/应用，`FIELD_REFERENCE` 与 `OUTPUT_CONTRACT` 是喂给
  模型的字段手册与输出契约，**新增节点类型或字段时这里必须同步改**，否则助手会瞎编字段名。
- `backend/app/services/workflow_store.py` — 工作流读写 + 引擎级校验
- `backend/app/services/prompt_store.py` — 提示词模板读写 + 引用反查
- `backend/app/api/prompts.py`、`backend/app/api/runs.py` — HTTP 层
- `frontend/components/CopilotPanel.tsx` — 提案卡片与确认流

## 五、离线自检

后端不联网、不花 token 的回归测试（openai / dotenv 缺失时自动打桩）：

```bash
cd ai-drama-studio/backend
python scripts/test_copilot.py
```

覆盖 34 项：循环依赖拒绝、未知类型拒绝、坏写入不留文件、模板注释头保留、模板引用保护、
非法提案拦截、改前快照、批量顺序应用、过期指纹拒绝、删节点自动改接下游、新建整条链路。

---

## 六、已知边界

- 一次提案改一份文档的一个方面，**跨工作流的大改**请分多次问。
- 助手看得见的是「当前选中的工作流 + 它引用的模板」，最多 3 份、每份 60KB（超出会截断）。
  要看全貌就先选中那条链路。
- `preview_gate` 之类的布尔/整数混合字段走聊天最稳；手填表单里布尔字段没开放，
  因为文本框里输 `false` 在 Python 是真值。
- LLM 通道沿用设置页配置：DeepSeek 失败会自动降级智谱 GLM-4-Flash（免费）。
