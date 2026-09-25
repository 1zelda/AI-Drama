"""ComfyUI integration based on OpenMontage pattern.

Handles: submit workflow -> poll/wait -> download artifacts.
Supports both REST polling and WebSocket progress streaming.
"""
from __future__ import annotations

import copy
import json
import os
import random
import time
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, List, Dict

import requests

try:
    import websocket
    HAS_WS = True
except ImportError:
    HAS_WS = False


class ComfyUIError(Exception):
    def __init__(self, message, prompt_id=None):
        super().__init__(message)
        self.prompt_id = prompt_id


class ComfyUIClient:
    """Thin REST client for a running ComfyUI server."""

    def __init__(self, server_url=None, capability=None):
        self.capability = capability
        self._env_var = f"COMFYUI_{capability.upper()}_SERVER_URL" if capability else None
        resolved = server_url or self._cap_url() or os.environ.get("COMFYUI_SERVER_URL") or "http://localhost:8188"
        self.server_url = resolved.rstrip("/")
        self.client_id = str(uuid.uuid4())

    def _cap_url(self):
        return os.environ.get(self._env_var) if self._env_var else None

    def is_available(self):
        try:
            r = requests.get(f"{self.server_url}/system_stats", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self):
        try:
            r = requests.get(f"{self.server_url}/models", timeout=10)
            return r.json()
        except Exception:
            return {"checkpoints": [], "loras": [], "vae": [], "clip": []}

    def submit(self, workflow):
        """Queue a workflow and return prompt_id."""
        w = copy.deepcopy(workflow)
        w["client_id"] = self.client_id
        r = requests.post(f"{self.server_url}/prompt", json={"prompt": w}, timeout=30)
        r.raise_for_status()
        return r.json()["prompt_id"]

    def poll(self, prompt_id, timeout=600, interval=5):
        """Poll /history until prompt completes or times out."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.server_url}/history/{prompt_id}", timeout=10)
                r.raise_for_status()
                data = r.json()
                if prompt_id in data:
                    return data[prompt_id]
            except Exception:
                pass
            time.sleep(interval)
        raise ComfyUIError(f"Timeout waiting for prompt {prompt_id}", prompt_id)

    def _wait_ws(self, prompt_id, timeout=600, on_progress=None):
        """Wait via WebSocket for completion (preferred over REST polling)."""
        if not HAS_WS:
            return self.poll(prompt_id, timeout=timeout)
        done = [False]

        def _on_msg(_, msg):
            if isinstance(msg, dict):
                if msg.get("type") == "executing" and msg.get("data", {}).get("node") is None:
                    done[0] = True
                elif msg.get("type") == "progress" and on_progress:
                    on_progress(msg)

        ws = websocket.WebSocketApp(f"{self.server_url}/ws/{self.client_id}", on_message=_on_msg)
        t = threading.Thread(target=ws.run_forever, kwargs={"timeout": timeout})
        t.start()
        deadline = time.time() + timeout
        while not done[0] and time.time() < deadline:
            time.sleep(0.1)
        ws.close()
        t.join(timeout=2)
        return {"outputs": {}}

    def generate(self, workflow, output_node, dest, *, timeout=600, resume_prompt_id=None, on_progress=None):
        """Submit -> wait -> download. Returns list of artifact paths."""
        prompt_id = resume_prompt_id or self.submit(workflow)
        if resume_prompt_id:
            entry = self.poll(prompt_id, timeout=timeout)
        else:
            entry = self._wait_ws(prompt_id, timeout=timeout, on_progress=on_progress)

        outputs = entry.get("outputs", {})
        node_out = outputs.get(output_node, {})
        items = node_out.get("images", []) or node_out.get("gifs", []) or node_out.get("audio", []) or node_out.get("video", [])
        if not items:
            raise ComfyUIError(f"No output on node {output_node}", prompt_id)

        paths = []
        for i, item in enumerate(items):
            suffix = Path(item["filename"]).suffix
            target = dest if len(items) == 1 else dest.with_stem(f"{dest.stem}_{i:03d}").with_suffix(suffix)
            self._download(item["filename"], item.get("subfolder", ""), target, item.get("type", "output"))
            paths.append(target)
        return paths

    def _download(self, filename, subfolder, dest, folder_type="output"):
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(f"{self.server_url}/view", params={"filename": filename, "subfolder": subfolder, "type": folder_type}, timeout=120)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest

    def upload_image(self, local_path, name):
        """Upload a local image so it can be referenced by LoadImage nodes."""
        with open(local_path, "rb") as f:
            r = requests.post(f"{self.server_url}/upload/image", files={"image": (name, f, "image/png")}, timeout=30)
        r.raise_for_status()
        return r.json()["name"]

    @staticmethod
    def load_workflow(path):
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def patch_workflow(workflow, patches):
        """Deep-copy workflow and apply patches."""
        w = copy.deepcopy(workflow)
        for nid, vals in patches.items():
            if nid not in w:
                raise ComfyUIError(f"Node {nid!r} not found. Available: {list(w.keys())}")
            for k, v in vals.items():
                w[nid]["inputs"][k] = v
        return w

    @staticmethod
    def random_seed():
        return random.randint(0, 2**32 - 1)