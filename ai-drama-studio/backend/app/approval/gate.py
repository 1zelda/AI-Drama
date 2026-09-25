"""Approval gate system - manages human-in-the-loop approval checkpoints."""
from __future__ import annotations
import os
import json
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ApprovalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


@dataclass
class ApprovalItem:
    id: str
    project_id: str
    stage: str  # "character", "episode", "storyboard", "image", "video"
    item_type: str
    item_id: str
    title: str
    description: str
    assets: List[Dict[str, Any]] = field(default_factory=list)  # generated images/videos
    status: str = "pending"
    feedback: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "stage": self.stage,
            "item_type": self.item_type,
            "item_id": self.item_id,
            "title": self.title,
            "description": self.description,
            "assets": self.assets,
            "status": self.status,
            "feedback": self.feedback,
            "created_at": self.created_at.isoformat(),
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approved_by": self.approved_by,
        }


class ApprovalGate:
    """Manages approval gates for a project."""

    def __init__(self, project_id: str, storage_dir: str):
        self.project_id = project_id
        self.storage_dir = Path(storage_dir) / project_id / "approvals"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._items: List[ApprovalItem] = []
        self._load()

    def _path(self) -> Path:
        return self.storage_dir / "approvals.json"

    def _load(self):
        if self._path().exists():
            data = json.loads(self._path().read_text(encoding="utf-8"))
            for item_data in data.get("items", []):
                item = ApprovalItem(**item_data)
                self._items.append(item)

    def _save(self):
        data = {"items": [item.to_dict() for item in self._items]}
        self._path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def create_item(self, stage: str, item_type: str, item_id: str, title: str, description: str, assets: List[Dict] = None) -> ApprovalItem:
        item = ApprovalItem(
            id=str(uuid.uuid4())[:8],
            project_id=self.project_id,
            stage=stage,
            item_type=item_type,
            item_id=item_id,
            title=title,
            description=description,
            assets=assets or [],
        )
        self._items.append(item)
        self._save()
        return item

    def get_pending(self, stage: Optional[str] = None) -> List[ApprovalItem]:
        items = [i for i in self._items if i.status == ApprovalStatus.PENDING.value]
        if stage:
            items = [i for i in items if i.stage == stage]
        return items

    def approve(self, item_id: str, approved_by: str = "user", feedback: str = "") -> Optional[ApprovalItem]:
        for item in self._items:
            if item.id == item_id:
                item.status = ApprovalStatus.APPROVED.value
                item.approved_at = datetime.now()
                item.approved_by = approved_by
                item.feedback = feedback
                self._save()
                return item
        return None

    def reject(self, item_id: str, feedback: str) -> Optional[ApprovalItem]:
        for item in self._items:
            if item.id == item_id:
                item.status = ApprovalStatus.REJECTED.value
                item.feedback = feedback
                self._save()
                return item
        return None

    def get_all(self) -> List[ApprovalItem]:
        return self._items

    def get_by_stage(self, stage: str) -> List[ApprovalItem]:
        return [i for i in self._items if i.stage == stage]