"""Pydantic models for API requests/responses."""
from pydantic import BaseModel
from typing import List, Optional, Any, Dict


class ProjectCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    genre: Optional[str] = ""


class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    plan: Optional[dict] = None
    characters: Optional[List[dict]] = None
    episodes: Optional[List[dict]] = None
    status: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    context: Optional[dict] = None


class ChatResponse(BaseModel):
    reply: str
    suggestions: Optional[List[str]] = None
    updated_project: Optional[dict] = None


class ApprovalAction(BaseModel):
    action: str  # approve, reject
    feedback: str = ""


class CharacterCreate(BaseModel):
    name: str
    role: str
    description: str
    appearance: str
    costume: str
    personality: str = ""


class EpisodeCreate(BaseModel):
    number: int
    title: str
    summary: str
    key_scenes: List[str] = []


class WorkflowExecuteRequest(BaseModel):
    workflow_name: str
    input_data: Dict[str, Any] = {}


class WorkflowStatusResponse(BaseModel):
    workflow_name: str
    status: str
    node_states: Dict[str, Any]
    output: Optional[Dict[str, Any]] = None