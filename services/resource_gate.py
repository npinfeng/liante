"""Shared limit for model inference pressure on one process."""

from __future__ import annotations

import threading
from contextlib import contextmanager


class ResourceBusyError(TimeoutError):
    pass


class ResourceGate:
    def __init__(self, slots: int = 1, wait_seconds: float = 30) -> None:
        self.slots = max(1, slots)
        self.wait_seconds = max(0, wait_seconds)
        self._semaphore = threading.BoundedSemaphore(self.slots)
        self._lock = threading.Lock()
        self._active = 0
        self._waiting = 0

    @contextmanager
    def enter(self):
        with self._lock:
            self._waiting += 1
        acquired = self._semaphore.acquire(timeout=self.wait_seconds)
        with self._lock:
            self._waiting -= 1
            if acquired:
                self._active += 1
        if not acquired:
            raise ResourceBusyError("模型推理资源等待超时")
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._semaphore.release()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(slots=self.slots, active=self._active, waiting=self._waiting)
