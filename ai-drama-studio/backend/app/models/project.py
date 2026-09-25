"""In-memory project store with JSON persistence."""
from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Project:
    id: str
    title: str
    description: str
    plan: Dict[str, Any] = field(default_factory=dict)
    characters: List[Dict[str, Any]] = field(default_factory=list)
    episodes: List[Dict[str, Any]] = field(default_factory=list)
    storyboard: List[Dict[str, Any]] = field(default_factory=list)
    assets: Dict[str, List[str]] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    status: str = "planning"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "plan": self.plan,
            "characters": self.characters,
            "episodes": self.episodes,
            "storyboard": self.storyboard,
            "assets": self.assets,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "status": self.status,
        }