"""Eye geometry over face landmarks, kept separate from face inference."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

from dms.detectors.face_landmarks import FaceLandmarkObservation, FaceLandmarksDetector


class EyeState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EyeObservation:
    state: EyeState
    ear: float | None
    left_ear: float | None
    right_ear: float | None
    reason: str | None


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def eye_aspect_ratio(points: Sequence[tuple[float, float]], min_width_px: float) -> float | None:
    """Calculate EAR for six points ordered around the eyelid."""
    if len(points) != 6:
        raise ValueError("EAR requires six ordered eyelid points")
    width = _distance(points[0], points[3])
    if not math.isfinite(width) or width < min_width_px:
        return None
    ratio = (_distance(points[1], points[5]) + _distance(points[2], points[4])) / (2.0 * width)
    return ratio if math.isfinite(ratio) else None


class EyeDetector:
    """Convert a normalized 478-point mesh into bilateral EAR observations."""

    _LEFT_EYE = (362, 385, 387, 263, 373, 380)
    _RIGHT_EYE = (33, 160, 158, 133, 153, 144)

    def __init__(self, ear_closed_threshold: float = 0.20, min_eye_width_px: float = 12.0) -> None:
        if not 0.0 < ear_closed_threshold < 1.0:
            raise ValueError("EAR threshold must be between 0 and 1")
        if min_eye_width_px <= 0:
            raise ValueError("minimum eye width must be positive")
        self.ear_closed_threshold = ear_closed_threshold
        self.min_eye_width_px = min_eye_width_px

    def observe(
        self,
        face: FaceLandmarkObservation,
        *,
        ear_closed_threshold: float | None = None,
        max_relative_asymmetry: float | None = None,
    ) -> EyeObservation:
        threshold = self.ear_closed_threshold if ear_closed_threshold is None else ear_closed_threshold
        if not math.isfinite(threshold) or not 0.0 < threshold < 1.0:
            raise ValueError("EAR threshold must be finite and between 0 and 1")
        if max_relative_asymmetry is not None and (
            not math.isfinite(max_relative_asymmetry)
            or not 0.0 < max_relative_asymmetry < 1.0
        ):
            raise ValueError("maximum relative EAR asymmetry must be between 0 and 1")
        if not face.valid:
            return self._unknown(face.reason or "face_landmarks_unavailable")
        landmarks = face.landmarks
        assert landmarks is not None
        if len(landmarks) <= max(*self._LEFT_EYE, *self._RIGHT_EYE):
            return self._unknown("unexpected_landmark_schema")

        left_points = self._pixel_points(landmarks, self._LEFT_EYE, face.width, face.height)
        right_points = self._pixel_points(landmarks, self._RIGHT_EYE, face.width, face.height)
        if left_points is None or right_points is None:
            return self._unknown("implausible_eye_landmarks")

        left_ear = eye_aspect_ratio(left_points, self.min_eye_width_px)
        right_ear = eye_aspect_ratio(right_points, self.min_eye_width_px)
        if left_ear is None or right_ear is None:
            return EyeObservation(
                EyeState.UNKNOWN,
                None,
                left_ear,
                right_ear,
                "eye_width_below_minimum_or_degenerate",
            )
        ear = (left_ear + right_ear) / 2.0
        if max_relative_asymmetry is not None:
            relative_asymmetry = abs(left_ear - right_ear) / max(ear, 1e-9)
            if relative_asymmetry > max_relative_asymmetry:
                return EyeObservation(
                    EyeState.UNKNOWN,
                    ear,
                    left_ear,
                    right_ear,
                    "bilateral_ear_disagreement",
                )
        # Both eyes must support closure; a wink or one-eye occlusion is not a
        # bilateral closure event.
        state = (
            EyeState.CLOSED
            if left_ear <= threshold and right_ear <= threshold
            else EyeState.OPEN
        )
        return EyeObservation(state, ear, left_ear, right_ear, None)

    @staticmethod
    def _pixel_points(landmarks, indices, width: int, height: int):
        points: list[tuple[float, float]] = []
        for index in indices:
            x = float(landmarks[index].x)
            y = float(landmarks[index].y)
            if not math.isfinite(x) or not math.isfinite(y) or not (-0.05 <= x <= 1.05 and -0.05 <= y <= 1.05):
                return None
            points.append((x * width, y * height))
        return points

    @staticmethod
    def _unknown(reason: str) -> EyeObservation:
        return EyeObservation(EyeState.UNKNOWN, None, None, None, reason)


class FaceLandmarkerEyes:
    """Compatibility facade used by the first eye-only milestone."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        ear_closed_threshold: float,
        min_eye_width_px: float,
        min_face_detection_confidence: float = 0.5,
    ) -> None:
        self._face = FaceLandmarksDetector(
            model_path,
            min_detection_confidence=min_face_detection_confidence,
            min_presence_confidence=min_face_detection_confidence,
        )
        self._eyes = EyeDetector(ear_closed_threshold, min_eye_width_px)

    def observe(self, bgr_image, timestamp_ms: int) -> EyeObservation:
        return self._eyes.observe(self._face.detect(bgr_image, timestamp_ms))

    def close(self) -> None:
        self._face.close()
