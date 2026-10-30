from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

from dms.alerts.states import AlertState


@dataclass(frozen=True, slots=True)
class Notification:
    channel: str
    level: str
    episode_id: str | None
    reasons: tuple[str, ...]
    requested_at_s: float


class NotificationQueue:
    """Bounded, coalescing handoff from decision loop to output adapters."""

    def __init__(self, maxsize: int = 8) -> None:
        self._queue: queue.Queue[Notification] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self.dropped = 0

    def request(self, alert: AlertState, timestamp_s: float) -> bool:
        if not alert.notify:
            return False
        notification = Notification(
            channel=alert.channel,
            level=alert.level.name.lower(),
            episode_id=alert.episode_id,
            reasons=alert.reasons,
            requested_at_s=timestamp_s,
        )
        try:
            self._queue.put_nowait(notification)
            return True
        except queue.Full:
            with self._lock:
                self.dropped += 1
            return False

    def next(self, timeout_s: float = 0.0) -> Notification | None:
        try:
            return self._queue.get(timeout=max(0.0, timeout_s))
        except queue.Empty:
            return None
