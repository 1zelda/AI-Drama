from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from .api.projects import router as projects_router
from .api.chat import router as chat_router
from .api.workflows import router as workflows_router
from .api import agnes as agnes_router
from .api.settings import router as settings_router

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

_static_dir = Path(__file__).parent.parent / 'static'
app.mount('/static', StaticFiles(directory=str(_static_dir)), name='static')

# Generated assets (character sheets, storyboard images, episode videos)
from .services.generation import ASSETS_DIR, OUTPUT_DIR
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/assets', StaticFiles(directory=str(ASSETS_DIR)), name='assets')
app.mount('/output', StaticFiles(directory=str(OUTPUT_DIR)), name='output')


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


@app.get('{full_path:path}', response_class=FileResponse)
async def catch_all(full_path: str):
    if full_path.startswith('/api'):
        raise HTTPException(status_code=404, detail='Not found')
    return FileResponse(str(_static_dir / 'index.html'), media_type='text/html')
