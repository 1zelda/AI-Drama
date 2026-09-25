"""Project persistence store backed by JSON files under data/projects/."""
from __future__ import annotations
import os
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from ..models.project import Project

# backend/data/projects  (store.py lives in backend/app/data/)
_DEFAULT_STORAGE = Path(__file__).resolve().parent.parent.parent / "data" / "projects"


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif not value:
        dt = datetime.now()
    else:
        try:
            dt = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            dt = datetime.now()
    # normalize to naive local so sorting never mixes aware/naive datetimes
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


class ProjectStore:
    """File-backed store for drama projects (one JSON file per project)."""

    def __init__(self, storage_dir: Optional[str] = None):
        self.storage_dir = Path(storage_dir) if storage_dir else _DEFAULT_STORAGE
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str) -> Path:
        return self.storage_dir / f"{project_id}.json"

    def _from_dict(self, data: dict) -> Project:
        data = dict(data)
        data["created_at"] = _parse_dt(data.get("created_at"))
        data["updated_at"] = _parse_dt(data.get("updated_at"))
        # drop unknown keys so the dataclass constructor never breaks on schema drift
        allowed = set(Project.__dataclass_fields__)
        data = {k: v for k, v in data.items() if k in allowed}
        return Project(**data)

    def _save(self, project: Project) -> Project:
        project.updated_at = datetime.now()
        self._path(project.id).write_text(
            json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return project

    def create(self, title: str, description: str = "") -> Project:
        now = datetime.now()
        project = Project(
            id=uuid.uuid4().hex[:8],
            title=title,
            description=description,
            created_at=now,
            updated_at=now,
            status="planning",
        )
        return self._save(project)

    def get(self, project_id: str) -> Optional[Project]:
        path = self._path(project_id)
        if not path.exists():
            return None
        try:
            return self._from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def list_all(self) -> List[Project]:
        projects: List[Project] = []
        for path in sorted(self.storage_dir.glob("*.json")):
            try:
                projects.append(self._from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return sorted(projects, key=lambda p: p.updated_at, reverse=True)

    def update(self, project_id: str, **updates) -> Optional[Project]:
        project = self.get(project_id)
        if not project:
            return None
        for key, value in updates.items():
            if key in ("id", "created_at") or value is None:
                continue
            if hasattr(project, key):
                setattr(project, key, value)
        return self._save(project)

    def update_plan(self, project_id: str, plan: dict) -> Optional[Project]:
        project = self.get(project_id)
        if not project:
            return None
        project.plan = plan
        project.characters = plan.get("characters", project.characters)
        project.episodes = plan.get("episodes", project.episodes)
        project.status = "planned"
        return self._save(project)

    def delete(self, project_id: str) -> bool:
        path = self._path(project_id)
        if path.exists():
            path.unlink()
            return True
        return False
