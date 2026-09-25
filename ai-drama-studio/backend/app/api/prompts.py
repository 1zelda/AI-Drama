"""提示词模板 API：让 llm 节点真正用的那段 prompt 能在前端/AI 助手里改。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from ..services import prompt_store

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


def _err(exc: prompt_store.StoreError) -> HTTPException:
    return HTTPException(404 if "不存在" in str(exc) else 400, str(exc))


@router.get("/")
async def list_prompts():
    return {"templates": prompt_store.list_templates()}


@router.get("/{key}")
async def get_prompt(key: str):
    try:
        return {"key": key, "doc": prompt_store.read(key)}
    except prompt_store.StoreError as exc:
        raise _err(exc)


@router.put("/{key}")
async def save_prompt(key: str, body: Dict[str, Any]):
    try:
        return {"saved": key, "doc": prompt_store.write(key, body)}
    except prompt_store.StoreError as exc:
        raise _err(exc)


@router.post("/{key}")
async def create_prompt(key: str, body: Dict[str, Any] = None):
    doc = body or prompt_store.blank(key)
    try:
        return {"created": key, "doc": prompt_store.write(key, doc, create=True)}
    except prompt_store.StoreError as exc:
        raise _err(exc)


@router.delete("/{key}")
async def delete_prompt(key: str):
    try:
        prompt_store.delete(key)
    except prompt_store.StoreError as exc:
        raise _err(exc)
    return {"deleted": key}
