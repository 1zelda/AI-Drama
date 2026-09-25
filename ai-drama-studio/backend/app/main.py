from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from .api.projects import router as projects_router
from .api.chat import router as chat_router
from .api.workflows import router as workflows_router
from .api import agnes as agnes_router
from .api.settings import router as settings_router
from .api.runs import router as runs_router
from .api.assets import router as assets_router
from .api.export_jianying import router as export_jianying_router
from .api.story import router as story_router
from .api.audio import router as audio_router
from .api.routing import router as routing_router
from .api.system import router as system_router
from .api.history import router as history_router
from .api.prompts import router as prompts_router
from .api.copilot import router as copilot_router

# 设置页保存的是 frontend/data/settings.json，而生图/生视频/配音通道读的是环境变量。
# 启动时先同步一次，否则会出现「自检说已配置、真跑起来报缺 Key」。
from .services import provider_picker as _provider_picker

_provider_picker.apply_settings_to_env()

app = FastAPI(
    title='AI Drama Studio',
    description='AI-powered drama creation platform with ComfyUI + Agnes integration',
    version='1.1.0',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(projects_router)
app.include_router(chat_router)
app.include_router(workflows_router)
app.include_router(agnes_router.router)
app.include_router(settings_router)
app.include_router(runs_router)
app.include_router(assets_router)
app.include_router(export_jianying_router)
app.include_router(story_router)
app.include_router(audio_router)
app.include_router(routing_router)
app.include_router(system_router)
app.include_router(history_router)
app.include_router(prompts_router)
app.include_router(copilot_router)

_static_dir = Path(__file__).parent.parent / 'static'
app.mount('/static', StaticFiles(directory=str(_static_dir)), name='static')

_media_dir = Path(__file__).parent.parent / 'data' / 'assets'
_media_dir.mkdir(parents=True, exist_ok=True)
app.mount('/media/assets', StaticFiles(directory=str(_media_dir)), name='media_assets')

# 流水线产物（output/<workflow>/<node>/...）也要能在画布里直接预览
_output_dir = Path(__file__).parent.parent / 'output'
_output_dir.mkdir(parents=True, exist_ok=True)
app.mount('/media/output', StaticFiles(directory=str(_output_dir)), name='media_output')


@app.middleware('http')
async def trailing_slash_middleware(request: Request, call_next):
    if not request.url.path.startswith('/api') and request.url.path != '/' and request.url.path.endswith('/'):
        new_path = request.url.path.rstrip('/')
        return JSONResponse(content={'redirect': new_path}, status_code=307, headers={'Location': new_path})
    response = await call_next(request)
    return response


@app.get('/', response_class=FileResponse)
async def root():
    return FileResponse(str(_static_dir / 'index.html'), media_type='text/html')


@app.get('/new', response_class=FileResponse)
async def new_page():
    return FileResponse(str(_static_dir / 'index.html'), media_type='text/html')


@app.get('/project/{project_id}', response_class=FileResponse)
async def project_page(project_id: str):
    return FileResponse(str(_static_dir / 'index.html'), media_type='text/html')


@app.get('/health')
async def health():
    from .providers.comfyui import ComfyUIClient
    from .providers.agnes import get_agnes_client
    comfy_ok = ComfyUIClient().is_available()
    agnes_ok = await get_agnes_client().health()
    return {
        'status': 'ok',
        'comfyui': 'available' if comfy_ok else 'unavailable',
        'comfyui_url': 'http://localhost:8188',
        'agnes': 'available' if agnes_ok else 'unavailable',
        'agnes_proxy': 'http://127.0.0.1:57324',
    }


@app.exception_handler(404)
async def spa_fallback(request: Request, exc):
    """非 API 的未知路径回落到 SPA index.html。

    注意：这里**不能**用 `@app.get('{full_path:path}')` 做通配兜底——它会精确匹配
    `/api/projects`（无尾斜杠）从而抢在真实 API 路由前面返回 404。改用 404 handler
    后，Starlette 内置的 redirect_slashes 才能把 `/api/projects` 307 到 `/api/projects/`。
    """
    if request.url.path.startswith('/api'):
        return JSONResponse(content={'detail': 'Not found'}, status_code=404)
    index = _static_dir / 'index.html'
    if index.exists():
        return FileResponse(str(index), media_type='text/html')
    return JSONResponse(content={'detail': 'Not found'}, status_code=404)
