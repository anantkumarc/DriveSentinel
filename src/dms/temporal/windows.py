"""Duration-weighted rolling eye window with explicit coverage."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from dms.detectors.eyes import EyeState


@dataclass(frozen=True, slots=True)
class WindowFeature:
    perclos: float | None
    closed_seconds: float
    observed_seconds: float
    coverage: float
    valid: bool


class PerclosWindow:
    """Estimate closure fraction over observed time, not frame counts."""

    def __init__(self, window_s: float = 60.0, max_gap_s: float = 0.25, min_coverage: float = 0.5) -> None:
        if window_s <= 0 or max_gap_s <= 0 or not 0.0 <= min_coverage <= 1.0:
            raise ValueError("invalid PERCLOS window configuration")
        self.window_s = window_s
        self.max_gap_s = max_gap_s
        self.min_coverage = min_coverage
        self._segments: deque[tuple[float, float, EyeState]] = deque()
        self._last_time_s: float | None = None
        self._last_state = EyeState.UNKNOWN

    def update(self, timestamp_s: float, state: EyeState) -> WindowFeature:
        if not math.isfinite(timestamp_s):
            raise ValueError("window timestamp must be finite")
        if self._last_time_s is not None:
            if timestamp_s <= self._last_time_s:
                raise ValueError("duplicate or out-of-order window timestamp")
            duration = timestamp_s - self._last_time_s
            if duration <= self.max_gap_s and self._last_state is not EyeState.UNKNOWN:
                self._segments.append((self._last_time_s, timestamp_s, self._last_state))
        self._last_time_s = timestamp_s
        self._last_state = state
        return self.value(timestamp_s)

    def value(self, now_s: float) -> WindowFeature:
        cutoff = now_s - self.window_s
        while self._segments and self._segments[0][1] <= cutoff:
            self._segments.popleft()
        closed_s = 0.0
        observed_s = 0.0
        for start, end, state in self._segments:
            overlap = max(0.0, min(end, now_s) - max(start, cutoff))
            if overlap <= 0:
                continue
            observed_s += overlap
            if state is EyeState.CLOSED:
                closed_s += overlap
        coverage = min(1.0, observed_s / self.window_s)
        valid = observed_s >= self.window_s * self.min_coverage
        perclos = closed_s / observed_s if valid and observed_s > 0 else None
        return WindowFeature(perclos, closed_s, observed_s, coverage, valid)
