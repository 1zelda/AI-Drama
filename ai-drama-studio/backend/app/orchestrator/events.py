"""In-process event bus for pipeline runs.

The engine emits events through ``on_event``; this bus fans them out to any
number of SSE subscribers and keeps a bounded history per run so a client that
connects late can replay what it missed.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

_HISTORY_LIMIT = 2000


class _RunState:
    __slots__ = ("history", "subscribers", "status", "created_at", "finished_at", "result", "error")

    def __init__(self) -> None:
        self.history: Deque[dict] = deque(maxlen=_HISTORY_LIMIT)
        self.subscribers: Dict[int, asyncio.Queue] = {}
        self.status: str = "running"
        self.created_at: float = time.time()
        self.finished_at: Optional[float] = None
        self.result: Optional[dict] = None
        self.error: Optional[str] = None


class EventBus:
    """Registry of active runs + pub/sub for their events. Thread-safe publish."""

    def __init__(self) -> None:
        self._runs: Dict[str, _RunState] = {}
        self._lock = threading.Lock()
        self._seq = 0

    # -- registration -------------------------------------------------- #

    def register(self, run_id: str) -> None:
        with self._lock:
            self._runs[run_id] = _RunState()

    def finish(self, run_id: str, status: str, result: Optional[dict] = None,
               error: Optional[str] = None) -> None:
        with self._lock:
            state = self._runs.get(run_id)
            if not state:
                return
            state.status = status
            state.finished_at = time.time()
            state.result = result
            state.error = error
        self.publish(run_id, {"type": "run_end", "status": status,
                              "error": error or ""})

    # -- publishing ----------------------------------------------------- #

    def publish(self, run_id: str, event: dict) -> None:
        """Called from the engine thread (possibly not the event loop's)."""
        with self._lock:
            state = self._runs.get(run_id)
            if not state:
                return
            self._seq += 1
            event = {**event, "seq": self._seq}
            state.history.append(event)
            queues = list(state.subscribers.values())
        for q in queues:
            try:
                q.put_nowait(event)
            except Exception:
                pass

    # -- querying -------------------------------------------------------- #

    def exists(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._runs

    def status(self, run_id: str) -> Optional[dict]:
        with self._lock:
            state = self._runs.get(run_id)
            if not state:
                return None
            return {
                "run_id": run_id,
                "status": state.status,
                "created_at": state.created_at,
                "finished_at": state.finished_at,
                "events": len(state.history),
                "result": state.result,
                "error": state.error,
            }

    def list_runs(self, limit: int = 50) -> List[dict]:
        with self._lock:
            items = sorted(self._runs.items(), key=lambda kv: kv[1].created_at, reverse=True)
            return [
                {"run_id": rid, "status": s.status, "created_at": s.created_at,
                 "finished_at": s.finished_at}
                for rid, s in items[:limit]
            ]

    def history(self, run_id: str) -> List[dict]:
        with self._lock:
            state = self._runs.get(run_id)
            return list(state.history) if state else []

    # -- subscribing (must be called on the event loop) ------------------ #

    def subscribe(self, run_id: str) -> "tuple[int, asyncio.Queue, List[dict]]":
        """Returns (subscriber_id, queue, replay_history)."""
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            state = self._runs.get(run_id)
            if not state:
                raise KeyError(run_id)
            sub_id = id(q)
            state.subscribers[sub_id] = q
            replay = list(state.history)
        return sub_id, q, replay

    def unsubscribe(self, run_id: str, sub_id: int) -> None:
        with self._lock:
            state = self._runs.get(run_id)
            if state:
                state.subscribers.pop(sub_id, None)


bus = EventBus()
