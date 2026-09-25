# AI Drama Studio - Architecture Design

## Core Question: Do we need both OpenMontage-style orchestration AND ComfyUI?

**Answer: Yes.** Here is why:

### Why ComfyUI is essential
- **Character consistency**: IP-Adapter / Reference-Only nodes let you lock a character\'s face/appearance across all generated images
- **Style control**: LoRA, ControlNet (Canny, Depth, Pose) give precise visual control
- **Local/private generation**: No API costs, no data leaving your machine
- **Iterative refinement**: Regenerate just one panel, adjust seed/parameters
- **Prompt engineering**: Full control over positive/negative prompts per shot

### Why OpenMontage-style orchestration is essential  
- **Multi-step pipeline**: LLM plan -> character design -> scene gen -> video -> stitch
- **Approval gates**: Human-in-the-loop at each stage (approve character, approve episode plan, approve storyboard)
- **DAG execution**: Parallel nodes where possible (generate 10 scenes simultaneously)
- **Cost tracking**: Know how much each generation step costs
- **Skill registry**: Reusable tools (character gen, scene gen, video gen, audio, etc.)

### Our Architecture

`
┌─────────────────────────────────────────────────────────────┐
│  Frontend (Next.js 15)                                       │
│  ┌──────────┬──────────┬────────────────┐                   │
│  │  Chat    │  Board   │   Editor       │                   │
│  │  Mode    │  Mode    │   Mode         │                   │
│  │  (begin) │ (creator)│  (expert)      │                   │
│  └────┬─────┴────┬─────┴────────┐       │                   │
│       │          │             │        │                   │
└───────┼──────────┼─────────────┼────────┘                   │
        │          │             │                            │
        ▼          ▼             ▼                            │
┌─────────────────────────────────────────────────────────┐   │
│  Backend (FastAPI)                                        │   │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │   │
│  │ LLM Planner │  │ Approval Gate│  │ Workflow Engine│  │   │
│  │ (GPT/Deep)  │  │ (human loop) │  │ (DAG + retry)  │  │   │
│  └──────┬──────┘  └──────┬───────┘  └───────┬────────┘  │   │
│         │                │                   │           │   │
│         ▼                ▼                   ▼           │   │
│  ┌──────────────────────────────────────────────────┐   │   │
│  │  Providers Layer                                   │   │   │
│  │  ├─ ComfyUIClient  (local IP-Adapter/ControlNet)   │   │   │
│  │  ├─ CharacterManager (reference image tracking)    │   │   │
│  │  ├─ KlingProvider  (cloud video)                   │   │   │
│  │  ├─ SeedanceProvider                             │   │   │
│  │  └─ VeoProvider                                  │   │   │
│  └──────────────────────────────────────────────────┘   │   │
└─────────────────────────────────────────────────────────┘   │
        │                                                    │
        ▼                                                    │
┌─────────────────────────────────────────────────────────┐   │
│  ComfyUI (localhost:8188)                                 │   │
│  ├─ Character Sheet (IP-Adapter + LoRA)                  │   │
│  ├─ Scene Generation (text-to-image)                     │   │
│  └─ Image-to-Video (Wan 2.2 I2V)                         │   │
└─────────────────────────────────────────────────────────┘   │
