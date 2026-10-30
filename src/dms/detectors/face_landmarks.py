"""MediaPipe Face Landmarker adapter with an explicit per-frame contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Landmark3D:
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class FaceLandmarkObservation:
    width: int
    height: int
    timestamp_ms: int
    landmarks: tuple[Landmark3D, ...] | None
    face_count: int
    reason: str | None
    transformation_matrix: tuple[tuple[float, ...], ...] | None = None

    @property
    def valid(self) -> bool:
        return self.landmarks is not None and self.width > 0 and self.height > 0


class FaceLandmarksDetector:
    """Load one model and run causal video-mode landmark inference."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        min_detection_confidence: float = 0.5,
        min_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Face Landmarker model not found: {path}. Follow models/README.md."
            )
        for name, value in (
            ("detection", min_detection_confidence),
            ("presence", min_presence_confidence),
            ("tracking", min_tracking_confidence),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"face {name} confidence must be between 0 and 1")

        import mediapipe as mp

        self._mp = mp
        self._last_timestamp_ms = -1
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=min_detection_confidence,
            min_face_presence_confidence=min_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
        )
        self._task = mp.tasks.vision.FaceLandmarker.create_from_options(options)

    def close(self) -> None:
        self._task.close()

    def detect(self, bgr_image: Any, timestamp_ms: int) -> FaceLandmarkObservation:
        import cv2

        height, width = bgr_image.shape[:2]
        timestamp_ms = int(timestamp_ms)
        if timestamp_ms <= self._last_timestamp_ms:
            raise ValueError("face detector timestamps must increase for every frame")
        self._last_timestamp_ms = timestamp_ms
        if width <= 0 or height <= 0:
            return FaceLandmarkObservation(width, height, timestamp_ms, None, 0, "invalid_frame_dimensions")

        rgb = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._task.detect_for_video(mp_image, timestamp_ms)
        if not result.face_landmarks:
            return FaceLandmarkObservation(width, height, timestamp_ms, None, 0, "face_not_detected")

        # num_faces=1 is intentional; changing driver/face ownership requires a
        # separately evaluated ROI policy.
        points = tuple(Landmark3D(float(p.x), float(p.y), float(p.z)) for p in result.face_landmarks[0])
        matrix = None
        if result.facial_transformation_matrixes:
            values = result.facial_transformation_matrixes[0]
            matrix = tuple(tuple(float(value) for value in row) for row in values)
        return FaceLandmarkObservation(width, height, timestamp_ms, points, len(result.face_landmarks), None, matrix)
