"""Causal eye-closure interval tracking with explicit UNKNOWN handling."""

from __future__ import annotations

import math
from dataclasses import dataclass

from dms.detectors.eyes import EyeObservation, EyeState


@dataclass(frozen=True, slots=True)
class ClosureDecision:
    state: EyeState
    observed_duration_s: float | None
    prolonged_candidate: bool
    reason: str | None
    event_id: str | None = None


class EyeClosureTimer:
    def __init__(self, critical_seconds: float, max_sample_gap_seconds: float) -> None:
        if critical_seconds <= 0:
            raise ValueError("critical closure duration must be positive")
        if max_sample_gap_seconds <= 0:
            raise ValueError("maximum sample gap must be positive")
        self.critical_seconds = critical_seconds
        self.max_sample_gap_seconds = max_sample_gap_seconds
        self._last_time_s: float | None = None
        self._closed_start_s: float | None = None
        self._event_counter = 0
        self._current_event_id: str | None = None

    def update(self, source_time_s: float, observation: EyeObservation) -> ClosureDecision:
        if not math.isfinite(source_time_s):
            raise ValueError("source timestamp must be finite")
        if self._last_time_s is not None and source_time_s <= self._last_time_s:
            raise ValueError("duplicate or out-of-order observation timestamp")

        gap_s = None if self._last_time_s is None else source_time_s - self._last_time_s
        if gap_s is not None and gap_s > self.max_sample_gap_seconds:
            self._closed_start_s = None
            self._current_event_id = None
        self._last_time_s = source_time_s

        if observation.state is EyeState.UNKNOWN:
            self._closed_start_s = None
            self._current_event_id = None
            return ClosureDecision(EyeState.UNKNOWN, None, False, observation.reason or "observation_unknown")

        if observation.state is EyeState.OPEN:
            self._closed_start_s = None
            self._current_event_id = None
            return ClosureDecision(EyeState.OPEN, 0.0, False, None)

        if self._closed_start_s is None:
            self._closed_start_s = source_time_s
            self._event_counter += 1
            self._current_event_id = f"closure-{self._event_counter:06d}"
        duration_s = max(0.0, source_time_s - self._closed_start_s)
        return ClosureDecision(
            EyeState.CLOSED,
            duration_s,
            duration_s >= self.critical_seconds,
            None,
            self._current_event_id,
        )
