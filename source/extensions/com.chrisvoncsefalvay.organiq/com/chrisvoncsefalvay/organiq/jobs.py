from __future__ import annotations

import threading
import time


class ActionCancelled(RuntimeError):
    pass


class ActionProgress:
    def __init__(self, status: str):
        self._lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.started_at = time.monotonic()
        self._completed = 0
        self._total = 1
        self._status = status

    def __call__(self, completed: int, total: int, status: str) -> None:
        self.report(completed, total, status)

    def report(self, completed: int, total: int, status: str) -> None:
        if self.cancel_event.is_set():
            raise ActionCancelled("action cancelled")
        with self._lock:
            self._completed = max(0, int(completed))
            self._total = max(1, int(total))
            self._status = status

    def cancel(self) -> None:
        self.cancel_event.set()

    def snapshot(self) -> tuple[float, str]:
        with self._lock:
            value = min(1.0, max(0.0, float(self._completed) / float(self._total)))
            elapsed = max(0.0, time.monotonic() - self.started_at)
            status = self._status
            if self.cancel_event.is_set():
                status = "cancelling"
            elif elapsed >= 30.0:
                status = f"{status} ({elapsed:.0f}s)"
            return value, status
