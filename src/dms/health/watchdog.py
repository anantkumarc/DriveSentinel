from __future__ import annotations

import time

from dms.capture.packets import CaptureSnapshot, CaptureStatus, FramePacket


class CaptureWatchdog:
    """Detect stale capture without treating stale observations as current."""

    def __init__(self, unavailable_after_s: float = 1.0) -> None:
        if unavailable_after_s <= 0:
            raise ValueError("capture timeout must be positive")
        self.unavailable_after_s = unavailable_after_s
        self._last_capture_ns: int | None = None

    def observe_packet(self, packet: FramePacket) -> None:
        self._last_capture_ns = packet.captured_monotonic_ns

    def health(self, snapshot: CaptureSnapshot, now_ns: int | None = None) -> tuple[str, float | None]:
        if snapshot.status in {CaptureStatus.UNAVAILABLE, CaptureStatus.FAILED, CaptureStatus.STOPPED}:
            age_s = None if self._last_capture_ns is None else self._age_s(now_ns)
            return "unavailable", age_s
        if self._last_capture_ns is None:
            return "starting", None
        age_s = self._age_s(now_ns)
        if age_s > self.unavailable_after_s:
            return "unavailable", age_s
        return "available", age_s

    def _age_s(self, now_ns: int | None) -> float:
        current = time.monotonic_ns() if now_ns is None else now_ns
        return max(0.0, (current - (self._last_capture_ns or current)) / 1_000_000_000.0)
