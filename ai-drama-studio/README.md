# AI Drama Studio

A complete AI-powered drama creation platform. Input a title, chat with AI to plan story/characters/episodes, then generate videos with ComfyUI.

## Architecture

```
User Input (Title) → LLM Planning → Character Design → Episode Plan → ComfyUI Generation → Video Output
                        ↓                    ↓                  ↓
                   Chat Interface      IP-Adapter           Image-to-Video
                   (3 modes)          (Consistency)        (Wan 2.2/Kling)
```

### Three UI Modes

1. **Chat Mode** (Default) - Conversation-driven planning with AI director
2. **Approval Board** - Visual review of generated assets with approve/reject
3. **Workflow Editor** (Advanced) - Configure ComfyUI node pipelines

### Tech Stack

- **Backend**: Python FastAPI + ComfyUI client (OpenMontage pattern)
- **Frontend**: Next.js 15 + Tailwind CSS
- **Image Gen**: ComfyUI (local) with IP-Adapter for character consistency
- **Video Gen**: ComfyUI Wan 2.2 I2V / Cloud APIs (Kling, Seedance, Veo)
- **LLM**: OpenAI-compatible (GPT-4o, DeepSeek, etc.)

## Quick Start

### 1. Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env
# Edit .env with your API keys
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000

### 3. ComfyUI (Optional but recommended)

```bash
# Install ComfyUI separately
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
python -m pip install -r requirements.txt
python main.py --listen
# Default: http://localhost:8188
```

## Project Structure

```
ai-drama-studio/
├── backend/
│   ├── app/
│   │   ├── api/           # FastAPI routes
│   │   ├── approval/      # Approval gate system
│   │   ├── data/          # Project storage
│   │   ├── llm/           # LLM planning services
│   │   ├── models/        # Pydantic models
│   │   ├── orchestrator/  # Workflow engine
│   │   ├── providers/     # ComfyUI, video providers
│   │   └── main.py        # FastAPI app
│   ├── config/
│   │   ├── comfyui_workflows/  # ComfyUI workflow templates
│   │   └── prompts/scripts/    # LLM prompt templates
│   └── requirements.txt
├── frontend/
│   ├── app/
│   │   ├── page.tsx            # Home / project list
│   │   ├── new/page.tsx        # New project form
│   │   └── project/[id]/page.tsx  # Main workspace (3 modes)
│   └── package.json
└── docs/
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/projects | List all projects |
| POST | /api/projects | Create project |
| GET | /api/projects/{id} | Get project details |
| POST | /api/projects/{id}/plan | Auto-generate drama plan |
| POST | /api/projects/{id}/chat | Chat with AI planner |
| GET | /api/projects/{id}/approvals | List approval items |
| POST | /api/projects/{id}/approvals/{id}/action | Approve/reject |
| GET | /api/workflows | List available workflows |

## ComfyUI Workflows

Three pre-configured workflows in `backend/config/comfyui_workflows/`:

- `character-sheet.json` - Character reference sheets with IP-Adapter support
- `scene-generation.json` - Scene/storyboard image generation
- `image-to-video.json` - Wan 2.2 image-to-video pipeline

## Configuration

Copy `.env.example` to `.env` and set your API keys. The platform supports:

- **OpenAI** (GPT-4o, GPT-4-turbo)
- **DeepSeek** (deepseek-chat, deepseek-reasoner) - free tier available
- Any OpenAI-compatible API endpoint

## License

MIT