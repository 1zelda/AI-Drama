# AI 漫剧/真人剧开源项目调研报告

## 一、项目总览（按热度排序）

| 项目 | Stars | Forks | 核心定位 |
|------|-------|-------|----------|
| HBAI-Ltd/Toonflow-app | 14,676 | 2,643 | 一站式AI短剧创作平台 |
| chatfire-AI/huobao-drama | 14,147 | 2,625 | AI短剧创作（参考原型） |
| xhongc/ai_story | 1,443 | 308 | Python/Django故事视频生成 |
| shuyu-labs/BigBanana-AI-Director | 1,763 | 293 | 漫剧工场，关键帧驱动 |
| LingyiChen-AI/AIComicBuilder | 1,814 | 310 | 漫剧生成器，多模型支持 |
| Forget-C/Jellyfish | 6,197 | 1,071 | 短剧生产工作区 |
| Anil-matcha/Open-AI-Micro-Drama-Generator | 470 | 91 | 多智能体微短剧生成 |
| JiaqiLi404/IAmDirector | 192 | 4 | 文字转视频前端参考 |
| murongg/openframe | 103 | 25 | AI漫剧创作工作台 |
| dav-niu474/huobao-drama-ai | 83 | 39 | Next.js多供应商架构 |
| XiakeMan777/xiakeman-ai-short-drama | 79 | 46 | 虾客漫，本地优先 |
| drasstry/shortdrama-pipeline | 113 | 10 | 后端流水线，CLI优先 |

## 二、核心功能对比

### 剧情规划
- **BigBanana**: 最完整，支持小说导入→自动分集→结构化剧本
- **Jellyfish**: 章节脚本→分镜拆解→一致性检查
- **AIComicBuilder**: TXT/DOCX/PDF上传→AI解析→智能分集
- **huobao-drama-ai**: 剧本上传→AI解析→角色/场景提取
- **ai_story**: 输入主题→自动文案创作+分镜设计
- **MicroDrama**: 一句话→自主agent完成全流程

### 人物一致性
- **BigBanana**: 角色定妆照+衣橱系统+资产库复用
- **Jellyfish**: 集中式角色/场景/道具/服装管理
- **AIComicBuilder**: 四视图参考图（正/3/侧/背）
- **xiakeman**: 人物参考图+场景图+故事板

### 视频生成
- **BigBanana**: 关键帧驱动（首尾帧插值）+ 九宫格分镜
- **huobao-drama-ai**: Seedance 2.0 / Kling / 多供应商
- **AIComicBuilder**: Seedance/Kling/Veo 多模型
- **Jellyfish**: 首帧+参考图生成，批量任务
- **shortdrama-pipeline**: Seedance 2.0 按shot生成→ffmpeg拼接

### 是否需要ComfyUI
- **BigBanana**: 否，使用AntSK API直接调用
- **Jellyfish**: 否，自研工作流引擎
- **huobao-drama-ai**: 否，多供应商API直连
- **ai_story**: 否，Django+Celery异步管道
- **部分辅助工具**: 可能集成ComfyUI做图片预处理

## 三、技术架构模式

### 模式A：全栈Web应用（主流）
- 前端：Next.js 16 / React + Vite
- 后端：FastAPI / Django / Next.js API Routes
- 数据库：PostgreSQL / SQLite
- 部署：Docker / Vercel

### 模式B：后端流水线
- Python + FastAPI/Django
- Celery异步任务队列
- FFmpeg视频拼接
- CLI优先，后续加Web

### 模式C：本地桌面应用
- Tauri/Electron
- 浏览器本地存储
- 自托管模型

## 四、你的项目差异化机会

现有项目大多侧重于"自动化生成"，缺少：
1. **对话式剧情规划** - 用AI助手跟你讨论剧情、集数、人物
2. **灵活的迭代确认** - 不是一键生成，而是分步骤讨论确认
3. **低门槛入口** - 只需一个标题，其余逐步引导
