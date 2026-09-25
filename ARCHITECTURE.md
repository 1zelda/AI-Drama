# AI 漫剧创作平台 - 项目架构设计

## 目标
输入一个标题 → AI帮你规划剧情/集数/人物 → 对话确认细节 → 生成视频

## 技术栈
- 前端: Next.js 16 + TypeScript + Tailwind CSS
- 后端: FastAPI + Python
- 数据库: SQLite (开发) / PostgreSQL (生产)
- AI: 多模型支持 (DeepSeek/GPT/Claude for 剧情, Kling/Seedance for 视频)
- 视频: FFmpeg 拼接

## 目录结构
\`\`\`
ai-drama-studio/
├── frontend/                 # Next.js 前端
│   ├── app/
│   │   ├── page.tsx          # 首页：输入标题
│   │   ├── project/
│   │   │   ├── [id]/         # 项目详情页
│   │   │   │   ├── planning/ # 剧情规划
│   │   │   │   ├── characters/# 人物设定
│   │   │   │   ├── episodes/ # 集数管理
│   │   │   │   └── generate/ # 视频生成
│   │   └── api/
│   ├── components/
│   │   ├── ChatDialog.tsx    # AI对话组件
│   │   ├── CharacterCard.tsx
│   │   ├── EpisodeCard.tsx
│   │   └── StoryBoard.tsx
│   └── lib/
├── backend/                  # FastAPI 后端
│   ├── app/
│   │   ├── api/
│   │   │   ├── projects.py
│   │   │   ├── chat.py       # 剧情对话API
│   │   │   └── generate.py   # 视频生成API
│   │   ├── models/
│   │   │   ├── project.py
│   │   │   ├── character.py
│   │   │   └── episode.py
│   │   ├── services/
│   │   │   ├── ai_planner.py  # AI剧情规划
│   │   │   ├── video_gen.py   # 视频生成
│   │   │   └── asset_mgmt.py  # 资产管理
│   │   └── main.py
│   └── requirements.txt
├── docker-compose.yml
└── README.md
\`\`\`

## 核心流程
1. 用户输入标题 → 调用AI规划剧情
2. AI输出：集数规划、人物设定、场景列表
3. 用户通过对话确认/修改
4. 确认后生成人物参考图
5. 生成每集分镜脚本
6. 逐镜头生成视频
7. 拼接成片

## 与现有项目的区别
- 更像"AI助手"而非"自动化工具"
- 强调对话式迭代，不是一键生成
- 低门槛：只需一个标题
