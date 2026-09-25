# 路线 C · Agent-技能化产线 — 使用手册

> 架构：**drama-skills 十技能（已安装）做创作大脑 → 本目录做后端配置与导出 → 剪映草稿人工终审**
> 详细调研依据见《架构升级调研与适配方案_v2.md》

## 一、这套产线怎么跑

```
小说/点子
  │  $short-drama-novel-analyze / $short-drama-develop     （分析原著 / 分集规划）
  ▼
剧本.md            $short-drama-write
  ▼
视觉设定.md         $short-drama-assets          ← 角色卡+参考包+锁定名词串（见 质量技巧与QC.md）
  ▼
图片提示词.md       $short-drama-image-prompts    ← 编号资产 C01/S01/P01 体系
  ▼
分镜.md            $short-drama-storyboard        ← 冻结关键帧 + 镜头交接单
  ▼
视频提示词.md       $short-drama-video-prompts     ← 首尾帧续接 / 一片段一动词 / Seedance 多镜头语法
  ▼
实际生成            $short-drama-produce           ← 先预览→显式确认→运行（付费前确认门）
  │                                                 生成后端见《生成后端配置.md》
  ▼
审查               $short-drama-review             ← 漂移日志 + 验收记录（放行门）
  ▼
导出剪映草稿        python scripts/export_jianying_draft.py   → 人工精剪/终审
```

每集状态 = 五份 Markdown（剧本/视觉设定/分镜/图片提示词/视频提示词），断点续跑天然支持——文件在，进度就在。

## 二、技能已装到哪里

以下技能已复制到 `C:\Users\Administrator\.agents\skills\`，**新开会话即可用**（本会话是装之前启动的，需重开会话才能自动触发；本会话内也可让我直接读技能文件执行）：

| 技能 | 职责 |
|---|---|
| short-drama | 总路由：项目初始化、跨阶段调度、Dashboard |
| short-drama-develop / novel-analyze | 点子开发 / 长篇原著分析与分集 |
| short-drama-write | 单集剧本 |
| short-drama-assets | 人物/造型/地点/道具资产拆解（连续性记录） |
| short-drama-image-prompts | 资产图片提示词（LookDev） |
| short-drama-storyboard | 镜头与冻结关键帧 |
| short-drama-video-prompts | 视频/时间线音乐提示词（含 MiniMax H3 / Seedance 方言） |
| short-drama-produce | 实际生成（预览→确认→运行） |
| short-drama-review | 审稿与校验 |
| onlyshot | 备选：即梦/Seedance 9:16 短剧技能（候选关键帧→审批→生成，含 17 种即梦故障模式手册） |

源码在 `_upgrade/drama-skills/`（MIT）与 `_upgrade/OnlyShot/`，要改技能直接改那里再重新复制。

**Windows 注意**：技能文档里的 `python3` 命令在本机用 `python`。

## 三、初始化一个新项目

```bash
python _upgrade/drama-skills/skills/short-drama/scripts/project_tool.py init ./我的新剧 \
  --title "我的新剧" --language zh --prompt-language zh --aspect-ratio 9:16 \
  --episode-count 10 --target-seconds 90
```

然后开会话直接说「继续我的短剧项目 ./我的新剧，写第 1 集剧本」即可按路由自动走。

## 四、本目录的三个组件

| 文件 | 用途 |
|---|---|
| `生成后端配置.md` | 云 API / 远程 ComfyUI 两条生成通道的选型、账号、工作流 JSON 直链 |
| `质量技巧与QC.md` | 15 条生产者技巧 + QC 五件套 + 九宫格法，产出提示词时必须遵守 |
| `scripts/export_jianying_draft.py` | 单集清单 JSON → 剪映草稿（视频轨/字幕轨/BGM 轨/转场），人工精剪接管 |

## 五、每集收尾：导出剪映草稿

生成完成后，把每集结果整理成 `单集清单.json`（格式见脚本头部注释），运行：

```bash
python drama-pipeline/scripts/export_jianying_draft.py 单集清单.json --draft-root "剪映草稿目录" --name "我的新剧EP01"
```

产出可直接被剪映 ≤6 打开的草稿（多轨、转场、字幕、BGM），也可加 `--export` 直接调起剪映自动导出 MP4。
