"""Mouth-opening geometry; temporal output remains a yawn candidate only."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from dms.detectors.face_landmarks import FaceLandmarkObservation


class MouthState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MouthObservation:
    state: MouthState
    opening_ratio: float | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class MouthCandidateDecision:
    state: MouthState
    opening_duration_s: float | None
    candidate_active: bool
    candidate_started: bool
    candidate_ended: bool
    event_id: int | None
    reason: str | None


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class MouthDetector:
    # Face Landmarker mesh indices: inner upper/lower lip and mouth corners.
    _UPPER_INNER_LIP = 13
    _LOWER_INNER_LIP = 14
    _LEFT_CORNER = 61
    _RIGHT_CORNER = 291

    def __init__(self, open_ratio_threshold: float = 0.55, min_mouth_width_px: float = 12.0) -> None:
        if open_ratio_threshold <= 0 or min_mouth_width_px <= 0:
            raise ValueError("mouth threshold and minimum width must be positive")
        self.open_ratio_threshold = open_ratio_threshold
        self.min_mouth_width_px = min_mouth_width_px

    def observe(self, face: FaceLandmarkObservation) -> MouthObservation:
        if not face.valid:
            return MouthObservation(MouthState.UNKNOWN, None, face.reason or "face_unavailable")
        landmarks = face.landmarks
        assert landmarks is not None
        required = (self._UPPER_INNER_LIP, self._LOWER_INNER_LIP, self._LEFT_CORNER, self._RIGHT_CORNER)
        if len(landmarks) <= max(required):
            return MouthObservation(MouthState.UNKNOWN, None, "unexpected_landmark_schema")
        points = []
        for index in required:
            landmark = landmarks[index]
            x, y = float(landmark.x), float(landmark.y)
            if not math.isfinite(x) or not math.isfinite(y) or not (-0.05 <= x <= 1.05 and -0.05 <= y <= 1.05):
                return MouthObservation(MouthState.UNKNOWN, None, "implausible_mouth_landmarks")
            points.append((x * face.width, y * face.height))
        upper, lower, left_corner, right_corner = points
        mouth_width = _distance(left_corner, right_corner)
        if mouth_width < self.min_mouth_width_px:
            return MouthObservation(MouthState.UNKNOWN, None, "mouth_width_below_minimum")
        ratio = _distance(upper, lower) / mouth_width
        if not math.isfinite(ratio):
            return MouthObservation(MouthState.UNKNOWN, None, "invalid_mouth_ratio")
        state = MouthState.OPEN if ratio >= self.open_ratio_threshold else MouthState.CLOSED
        return MouthObservation(state, ratio, None)


class MouthCandidateTracker:
    """Emit one opening candidate per continuous mouth-opening episode."""

    def __init__(self, candidate_after_s: float = 1.0, max_sample_gap_s: float = 0.25) -> None:
        if candidate_after_s <= 0 or max_sample_gap_s <= 0:
            raise ValueError("mouth timing parameters must be positive")
        self.candidate_after_s = candidate_after_s
        self.max_sample_gap_s = max_sample_gap_s
        self._last_time_s: float | None = None
        self._open_start_s: float | None = None
        self._candidate_emitted = False
        self._event_id = 0

    def update(self, timestamp_s: float, observation: MouthObservation) -> MouthCandidateDecision:
        if not math.isfinite(timestamp_s):
            raise ValueError("mouth timestamp must be finite")
        if self._last_time_s is not None and timestamp_s <= self._last_time_s:
            raise ValueError("duplicate or out-of-order mouth timestamp")

        gap = None if self._last_time_s is None else timestamp_s - self._last_time_s
        if gap is not None and gap > self.max_sample_gap_s:
            self._open_start_s = None
            self._candidate_emitted = False
        self._last_time_s = timestamp_s

        if observation.state is MouthState.UNKNOWN:
            ended = self._candidate_emitted
            event_id = self._event_id if ended else None
            self._open_start_s = None
            self._candidate_emitted = False
            return MouthCandidateDecision(MouthState.UNKNOWN, None, False, False, ended, event_id, observation.reason)

        if observation.state is MouthState.CLOSED:
            ended = self._candidate_emitted
            event_id = self._event_id if ended else None
            self._open_start_s = None
            self._candidate_emitted = False
            return MouthCandidateDecision(MouthState.CLOSED, 0.0, False, False, ended, event_id, None)

        if self._open_start_s is None:
            self._open_start_s = timestamp_s
        duration_s = timestamp_s - self._open_start_s
        started = duration_s >= self.candidate_after_s and not self._candidate_emitted
        if started:
            self._event_id += 1
            self._candidate_emitted = True
        return MouthCandidateDecision(
            MouthState.OPEN,
            duration_s,
            self._candidate_emitted,
            started,
            False,
            self._event_id if self._candidate_emitted else None,
            None,
        )
