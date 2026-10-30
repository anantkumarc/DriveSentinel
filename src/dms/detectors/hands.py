"""Optional MediaPipe hand-box adapter used for interaction association."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class NormalizedBox:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class HandObservation:
    valid: bool
    boxes: tuple[NormalizedBox, ...]
    reason: str | None


class HandLandmarksDetector:
    def __init__(self, model_path: str | Path, max_hands: int = 2) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"Hand Landmarker model not found: {path}")
        import mediapipe as mp

        self._mp = mp
        self._last_timestamp_ms = -1
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=max_hands,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._task = mp.tasks.vision.HandLandmarker.create_from_options(options)

    def close(self) -> None:
        self._task.close()

    def observe(self, bgr_image: Any, timestamp_ms: int) -> HandObservation:
        import cv2

        timestamp_ms = int(timestamp_ms)
        if timestamp_ms <= self._last_timestamp_ms:
            raise ValueError("hand detector timestamps must increase")
        self._last_timestamp_ms = timestamp_ms
        rgb = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._task.detect_for_video(image, timestamp_ms)
        boxes = []
        for hand in result.hand_landmarks:
            xs = [float(point.x) for point in hand]
            ys = [float(point.y) for point in hand]
            if not xs or not ys:
                continue
            boxes.append(
                NormalizedBox(
                    max(0.0, min(1.0, min(xs))),
                    max(0.0, min(1.0, min(ys))),
                    max(0.0, min(1.0, max(xs))),
                    max(0.0, min(1.0, max(ys))),
                )
            )
        return HandObservation(True, tuple(boxes), None)
