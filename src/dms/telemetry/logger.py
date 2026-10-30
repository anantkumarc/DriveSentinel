from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any


class JsonlWriter:
    """Write structured metadata asynchronously through a bounded queue."""

    def __init__(self, path: str | Path, queue_size: int = 512) -> None:
        self.path = Path(path)
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._dropped_records = 0
        self._error: str | None = None
        self._closed = False
        self._file = self.path.open("x", encoding="utf-8")
        self._thread = threading.Thread(
            target=self._write_loop,
            name="dms-jsonl-writer",
            daemon=True,
        )
        self._thread.start()

    @property
    def dropped_records(self) -> int:
        with self._lock:
            return self._dropped_records

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    def write(self, record: dict[str, Any]) -> bool:
        if self._closed or self.error is not None:
            with self._lock:
                self._dropped_records += 1
            return False
        try:
            self._queue.put_nowait(record)
            return True
        except queue.Full:
            with self._lock:
                self._dropped_records += 1
            return False

    def close(self, timeout_s: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(max(0.0, timeout_s))
        if not self._thread.is_alive():
            self._file.close()

    def _write_loop(self) -> None:
        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    record = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                line = json.dumps(record, separators=(",", ":"), allow_nan=False)
                self._file.write(line + "\n")
            self._file.flush()
        except Exception as exc:
            with self._lock:
                self._error = f"log writer failed: {exc}"
        finally:
            try:
                self._file.flush()
            except Exception:
                pass
