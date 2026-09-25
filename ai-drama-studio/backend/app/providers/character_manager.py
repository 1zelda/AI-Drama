"""Character consistency manager - tracks character reference images for ComfyUI IP-Adapter."""
from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Character:
    id: str
    name: str
    role: str  # protagonist, antagonist, supporting
    description: str
    appearance: str  # visual description for prompts
    costume: str
    personality: str
    reference_images: List[str] = field(default_factory=list)  # paths to generated reference images
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        # JSON 里读回来是字符串；不转成 datetime，下一次 _save() 会在
        # isoformat() 上炸掉（'str' object has no attribute 'isoformat'），
        # 表现为「加过角色之后再也无法登记参考图」。
        for name in ("created_at", "updated_at"):
            value = getattr(self, name)
            if isinstance(value, str):
                try:
                    setattr(self, name, datetime.fromisoformat(value))
                except ValueError:
                    setattr(self, name, datetime.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "appearance": self.appearance,
            "costume": self.costume,
            "personality": self.personality,
            "reference_images": self.reference_images,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class CharacterManager:
    """Manages character profiles and their reference images for consistency."""

    def __init__(self, project_id: str, storage_dir: str):
        self.project_id = project_id
        self.storage_dir = Path(storage_dir) / project_id / "characters"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._characters: Dict[str, Character] = {}
        self._load()

    def _path(self) -> Path:
        return self.storage_dir / "characters.json"

    def _load(self):
        if self._path().exists():
            data = json.loads(self._path().read_text(encoding="utf-8"))
            for c in data.get("characters", []):
                self._characters[c["id"]] = Character(**c)

    def _save(self):
        data = {"characters": [c.to_dict() for c in self._characters.values()]}
        self._path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_character(self, name: str, role: str, description: str, appearance: str, costume: str, personality: str = "") -> Character:
        import uuid
        char_id = str(uuid.uuid4())[:8]
        char = Character(
            id=char_id, name=name, role=role, description=description,
            appearance=appearance, costume=costume, personality=personality,
        )
        self._characters[char_id] = char
        self._save()
        return char

    def update_character(self, char_id: str, **kwargs) -> Optional[Character]:
        char = self._characters.get(char_id)
        if not char:
            return None
        for k, v in kwargs.items():
            if hasattr(char, k):
                setattr(char, k, v)
        char.updated_at = datetime.now()
        self._save()
        return char

    def add_reference_image(self, char_id: str, image_path: str) -> bool:
        char = self._characters.get(char_id)
        if not char:
            return False
        if image_path not in char.reference_images:
            char.reference_images.append(image_path)
        char.updated_at = datetime.now()
        self._save()
        return True

    def get_character(self, char_id: str) -> Optional[Character]:
        return self._characters.get(char_id)

    def list_characters(self) -> List[Character]:
        return list(self._characters.values())

    def get_reference_paths(self, char_id: str) -> List[str]:
        char = self._characters.get(char_id)
        return char.reference_images if char else []

    def export_for_comfyui(self) -> Dict[str, dict]:
        """Export character data formatted for ComfyUI IP-Adapter prompts."""
        result = {}
        for char_id, char in self._characters.items():
            result[char_id] = {
                "name": char.name,
                "appearance_prompt": self._build_appearance_prompt(char),
                "reference_images": char.reference_images,
            }
        return result

    def _build_appearance_prompt(self, char: Character) -> str:
        parts = []
        if char.appearance:
            parts.append(char.appearance)
        if char.costume:
            parts.append(char.costume)
        if char.personality:
            parts.append(f"expressing {char.personality}")
        return ", ".join(parts) if parts else "detailed portrait"