import threading
import time
from typing import Optional

from .persistent_log import write_log_entry


class ProgressTracker:
    _instance: Optional["ProgressTracker"] = None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self.status = "Idle"
        self.progress = 0.0
        self.logs: list[str] = []
        self.is_running = False
        self.error: Optional[str] = None
        self.result: Optional[bool] = None
        self._mesh_data = None

    def set_mesh_data(self, mesh_data) -> None:
        with self._lock:
            self._mesh_data = mesh_data

    def pop_mesh_data(self):
        with self._lock:
            mesh_data = self._mesh_data
            self._mesh_data = None
        return mesh_data

    @classmethod
    def get_instance(cls) -> "ProgressTracker":
        if cls._instance is None:
            cls._instance = ProgressTracker()
        return cls._instance

    def log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 50:
                self.logs = self.logs[-50:]
        write_log_entry(entry)
        print(entry)

    def set_status(self, status: str, progress: Optional[float] = None) -> None:
        with self._lock:
            self.status = status
            if progress is not None:
                self.progress = max(0.0, min(1.0, progress))

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def reset(self) -> None:
        with self._lock:
            self.status = "Idle"
            self.progress = 0.0
            self.logs = []
            self.is_running = False
            self.error = None
            self.result = None
            self._mesh_data = None
            self._cancel_event.clear()
