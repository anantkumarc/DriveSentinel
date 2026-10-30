"""Opt-in, in-memory calibration of an individual's open-eye EAR baseline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from statistics import median

from dms.detectors.eyes import EyeObservation


class EyeCalibrationState(str, Enum):
    IDLE = "idle"
    COLLECTING = "collecting"
    READY = "ready"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class EyeCalibrationSnapshot:
    state: EyeCalibrationState
    elapsed_s: float
    duration_s: float
    sample_count: int
    open_ear: float | None
    closed_threshold: float | None
    relative_mad: float | None
    reason: str | None


class EyeThresholdCalibrator:
    """Collect a stable open-eye EAR baseline after an explicit operator action.

    Samples remain in memory only during enrollment and are cleared after its
    outcome. Calibration succeeds only after the full capture interval, enough
    bilateral observations, a plausible baseline, and a sufficiently stable
    robust spread. It never learns outside an explicitly started window.
    """

    def __init__(
        self,
        *,
        duration_s: float = 20.0,
        closed_ratio: float = 0.70,
        min_samples: int = 50,
        min_open_ear: float = 0.12,
        max_open_ear: float = 0.55,
        max_relative_mad: float = 0.20,
        max_relative_asymmetry: float = 0.40,
    ) -> None:
        if not math.isfinite(duration_s) or not 20.0 <= duration_s <= 30.0:
            raise ValueError("eye calibration duration must be between 20 and 30 seconds")
        if not math.isfinite(closed_ratio) or not 0.50 <= closed_ratio < 1.0:
            raise ValueError("personalized closed ratio must be between 0.50 and 1.0")
        if min_samples < 2:
            raise ValueError("eye calibration requires at least two samples")
        if not 0.0 < min_open_ear < max_open_ear <= 1.0:
            raise ValueError("invalid plausible open-eye EAR range")
        if not 0.0 < max_relative_mad < 1.0:
            raise ValueError("maximum relative MAD must be between 0 and 1")
        if not 0.0 < max_relative_asymmetry < 1.0:
            raise ValueError("maximum EAR asymmetry must be between 0 and 1")

        self.duration_s = duration_s
        self.closed_ratio = closed_ratio
        self.min_samples = min_samples
        self.min_open_ear = min_open_ear
        self.max_open_ear = max_open_ear
        self.max_relative_mad = max_relative_mad
        self.max_relative_asymmetry = max_relative_asymmetry
        self._state = EyeCalibrationState.IDLE
        self._start_s: float | None = None
        self._samples: list[float] = []
        self._sample_count = 0
        self._open_ear: float | None = None
        self._closed_threshold: float | None = None
        self._relative_mad: float | None = None
        self._reason: str | None = None

    @property
    def state(self) -> EyeCalibrationState:
        return self._state

    def start(self, timestamp_s: float) -> EyeCalibrationSnapshot:
        if not math.isfinite(timestamp_s):
            raise ValueError("calibration timestamp must be finite")
        self._state = EyeCalibrationState.COLLECTING
        self._start_s = timestamp_s
        self._samples.clear()
        self._sample_count = 0
        self._open_ear = None
        self._closed_threshold = None
        self._relative_mad = None
        self._reason = None
        return self.snapshot(timestamp_s)

    def cancel(self, timestamp_s: float) -> EyeCalibrationSnapshot:
        if not math.isfinite(timestamp_s):
            raise ValueError("calibration timestamp must be finite")
        if self._state is EyeCalibrationState.COLLECTING:
            self._state = EyeCalibrationState.CANCELLED
            self._reason = "cancelled_by_operator"
            self._samples.clear()
        return self.snapshot(timestamp_s)

    def update(self, timestamp_s: float, observation: EyeObservation) -> EyeCalibrationSnapshot:
        if not math.isfinite(timestamp_s):
            raise ValueError("calibration timestamp must be finite")
        if self._state is not EyeCalibrationState.COLLECTING or self._start_s is None:
            return self.snapshot(timestamp_s)

        elapsed = timestamp_s - self._start_s
        if elapsed < 0.0:
            self._reject("non_monotonic_calibration_time")
            return self.snapshot(timestamp_s)

        left, right, ear = observation.left_ear, observation.right_ear, observation.ear
        if left is not None and right is not None and ear is not None:
            values = (float(left), float(right), float(ear))
            if all(math.isfinite(value) for value in values) and ear > 0.0:
                asymmetry = abs(left - right) / max((left + right) / 2.0, 1e-9)
                if (
                    self.min_open_ear <= ear <= self.max_open_ear
                    and asymmetry <= self.max_relative_asymmetry
                ):
                    if len(self._samples) < 20_000:
                        self._samples.append(float(ear))
                        self._sample_count += 1

        if elapsed >= self.duration_s:
            self._finish()
        return self.snapshot(timestamp_s)

    def snapshot(self, timestamp_s: float) -> EyeCalibrationSnapshot:
        if not math.isfinite(timestamp_s):
            raise ValueError("calibration timestamp must be finite")
        elapsed = 0.0 if self._start_s is None else max(0.0, timestamp_s - self._start_s)
        return EyeCalibrationSnapshot(
            state=self._state,
            elapsed_s=min(elapsed, self.duration_s),
            duration_s=self.duration_s,
            sample_count=self._sample_count,
            open_ear=self._open_ear,
            closed_threshold=self._closed_threshold,
            relative_mad=self._relative_mad,
            reason=self._reason,
        )

    def _finish(self) -> None:
        if len(self._samples) < self.min_samples:
            self._reject("insufficient_valid_bilateral_samples")
            return

        baseline = median(self._samples)
        mad = median(abs(sample - baseline) for sample in self._samples)
        relative_mad = 1.4826 * mad / baseline if baseline > 0.0 else math.inf
        self._relative_mad = relative_mad if math.isfinite(relative_mad) else None
        if not self.min_open_ear <= baseline <= self.max_open_ear:
            self._reject("open_eye_baseline_outside_plausible_range")
            return
        if not math.isfinite(relative_mad) or relative_mad > self.max_relative_mad:
            self._reject("open_eye_baseline_not_stable")
            return

        self._open_ear = baseline
        self._closed_threshold = baseline * self.closed_ratio
        self._state = EyeCalibrationState.READY
        self._reason = None
        self._samples.clear()

    def _reject(self, reason: str) -> None:
        self._state = EyeCalibrationState.REJECTED
        self._reason = reason
        self._samples.clear()
