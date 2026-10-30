"""Face-landmark head pose with automatic neutral calibration and solvePnP fallback."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dms.detectors.face_landmarks import FaceLandmarkObservation


@dataclass(frozen=True, slots=True)
class CameraCalibration:
    width: int
    height: int
    camera_matrix: tuple[tuple[float, ...], ...]
    distortion: tuple[float, ...]
    reprojection_rms_px: float | None

    @classmethod
    def load(cls, path: str | Path) -> "CameraCalibration":
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        matrix = tuple(tuple(float(value) for value in row) for row in data["camera_matrix"])
        distortion = tuple(float(value) for value in data.get("distortion", []))
        if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
            raise ValueError("camera_matrix must be 3 by 3")
        if not all(math.isfinite(value) for row in matrix for value in row):
            raise ValueError("camera_matrix contains a non-finite value")
        if not all(math.isfinite(value) for value in distortion):
            raise ValueError("distortion contains a non-finite value")
        width, height = int(data["image_width"]), int(data["image_height"])
        if width <= 0 or height <= 0:
            raise ValueError("calibration image dimensions must be positive")
        return cls(width, height, matrix, distortion, data.get("reprojection_rms_px"))


@dataclass(frozen=True, slots=True)
class HeadPoseObservation:
    valid: bool
    yaw_deg: float | None
    pitch_deg: float | None
    roll_deg: float | None
    relative_yaw_deg: float | None
    relative_pitch_deg: float | None
    relative_roll_deg: float | None
    reprojection_error_px: float | None
    rotation_matrix: tuple[tuple[float, ...], ...] | None
    reason: str | None


class HeadPoseEstimator:
    """Estimate pose from six generic 3D face points and calibrated camera K.

    Generic face geometry is only an initialization. Relative angles and any
    event thresholds require validation with the actual camera and pose labels.
    """

    # Nose, chin, eye corners, and mouth corners in Face Landmarker mesh.
    _IMAGE_INDICES = (1, 152, 33, 263, 61, 291)
    # Approximate generic face geometry in millimetres; not a subject-specific
    # model and not evidence of gaze or distraction.
    _MODEL_POINTS = (
        (0.0, 0.0, 0.0),
        (0.0, -330.0, -65.0),
        (-225.0, 170.0, -135.0),
        (225.0, 170.0, -135.0),
        (-150.0, -150.0, -125.0),
        (150.0, -150.0, -125.0),
    )

    def __init__(
        self,
        calibration: CameraCalibration | None,
        neutral_rotation: tuple[tuple[float, ...], ...] | None = None,
        max_reprojection_error_px: float = 12.0,
        neutral_calibration_seconds: float = 2.0,
    ) -> None:
        if max_reprojection_error_px <= 0 or neutral_calibration_seconds <= 0:
            raise ValueError("pose error limit and neutral calibration duration must be positive")
        self.calibration = calibration
        self.max_reprojection_error_px = max_reprojection_error_px
        self.neutral_rotation = neutral_rotation
        self.neutral_calibration_seconds = neutral_calibration_seconds
        self._neutral_samples: list[Any] = []
        self._neutral_started_ms: int | None = None
        self._neutral_last_ms: int | None = None

    @property
    def neutral_ready(self) -> bool:
        return self.neutral_rotation is not None

    def reset_neutral(self) -> None:
        """Restart the in-memory forward-facing baseline enrollment."""
        self.neutral_rotation = None
        self._neutral_samples.clear()
        self._neutral_started_ms = None
        self._neutral_last_ms = None

    @staticmethod
    def load_neutral(path: str | Path | None) -> tuple[tuple[float, ...], ...] | None:
        if path is None:
            return None
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        matrix = tuple(tuple(float(v) for v in row) for row in data["neutral_rotation_matrix"])
        if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
            raise ValueError("neutral_rotation_matrix must be 3 by 3")
        return matrix

    def estimate(self, face: FaceLandmarkObservation) -> HeadPoseObservation:
        if not face.valid:
            return self._unknown(face.reason or "face_landmarks_unavailable")
        if face.transformation_matrix is not None and (self.calibration is None or self.neutral_rotation is None):
            return self._estimate_from_facial_matrix(face)
        if self.calibration is None:
            return self._unknown("camera_calibration_missing_and_face_matrix_unavailable")
        if (face.width, face.height) != (self.calibration.width, self.calibration.height):
            return self._unknown("camera_calibration_resolution_mismatch")
        landmarks = face.landmarks
        assert landmarks is not None
        if len(landmarks) <= max(self._IMAGE_INDICES):
            return self._unknown("unexpected_landmark_schema")

        import cv2
        import numpy as np

        image_points = []
        for index in self._IMAGE_INDICES:
            x, y = float(landmarks[index].x), float(landmarks[index].y)
            if not math.isfinite(x) or not math.isfinite(y) or not (-0.05 <= x <= 1.05 and -0.05 <= y <= 1.05):
                return self._unknown("implausible_pose_landmarks")
            image_points.append((x * face.width, y * face.height))

        object_points = np.asarray(self._MODEL_POINTS, dtype=np.float64)
        image_points_array = np.asarray(image_points, dtype=np.float64)
        camera_matrix = np.asarray(self.calibration.camera_matrix, dtype=np.float64)
        distortion = np.asarray(self.calibration.distortion or (0.0, 0.0, 0.0, 0.0, 0.0), dtype=np.float64)
        success, rvec, tvec = cv2.solvePnP(
            object_points,
            image_points_array,
            camera_matrix,
            distortion,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            return self._unknown("solvepnp_failed")
        rotation, _ = cv2.Rodrigues(rvec)
        projected, _ = cv2.projectPoints(object_points, rvec, tvec, camera_matrix, distortion)
        error_px = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - image_points_array) ** 2, axis=1))))
        if not math.isfinite(error_px) or error_px > self.max_reprojection_error_px:
            return self._unknown("pose_reprojection_error_too_high", error_px)

        yaw, pitch, roll = self._euler_degrees(rotation)
        relative = None
        if self.neutral_rotation is not None:
            neutral = np.asarray(self.neutral_rotation, dtype=np.float64)
            relative = self._relative_euler_degrees(rotation, neutral)
        matrix_tuple = tuple(tuple(float(v) for v in row) for row in rotation)
        return HeadPoseObservation(
            True,
            yaw,
            pitch,
            roll,
            None if relative is None else relative[0],
            None if relative is None else relative[1],
            None if relative is None else relative[2],
            error_px,
            matrix_tuple,
            None if relative is not None else "neutral_reference_missing",
        )

    def _estimate_from_facial_matrix(self, face: FaceLandmarkObservation) -> HeadPoseObservation:
        import numpy as np

        matrix = np.asarray(face.transformation_matrix, dtype=np.float64)
        if matrix.shape == (4, 4):
            rotation = matrix[:3, :3]
        elif matrix.shape == (3, 3):
            rotation = matrix
        else:
            return self._unknown("invalid_facial_transformation_matrix")
        if not np.isfinite(rotation).all():
            return self._unknown("non_finite_facial_transformation_matrix")
        try:
            left, _, right = np.linalg.svd(rotation)
        except np.linalg.LinAlgError:
            return self._unknown("facial_transformation_matrix_decomposition_failed")
        rotation = left @ right
        if np.linalg.det(rotation) < 0:
            left[:, -1] *= -1
            rotation = left @ right

        timestamp_ms = face.timestamp_ms
        if self.neutral_rotation is None:
            self._capture_neutral_sample(rotation, timestamp_ms)
            yaw, pitch, roll = self._euler_degrees(rotation)
            return HeadPoseObservation(
                True, yaw, pitch, roll, None, None, None, None,
                tuple(tuple(float(value) for value in row) for row in rotation),
                "neutral_pose_calibrating",
            )

        neutral = np.asarray(self.neutral_rotation, dtype=np.float64)
        relative = self._relative_euler_degrees(rotation, neutral)
        yaw, pitch, roll = self._euler_degrees(rotation)
        return HeadPoseObservation(
            True,
            yaw,
            pitch,
            roll,
            relative[0],
            relative[1],
            relative[2],
            None,
            tuple(tuple(float(value) for value in row) for row in rotation),
            None,
        )

    def _capture_neutral_sample(self, rotation: Any, timestamp_ms: int) -> None:
        import numpy as np

        if self._neutral_last_ms is not None and timestamp_ms - self._neutral_last_ms > 500:
            self._neutral_samples.clear()
            self._neutral_started_ms = None
        self._neutral_last_ms = timestamp_ms
        if not self._neutral_samples:
            self._neutral_started_ms = timestamp_ms
            self._neutral_samples = [rotation.copy()]
            return
        first = self._neutral_samples[0]
        deviation = self._relative_euler_degrees(rotation, first)
        if max(abs(value) for value in deviation) > 8.0:
            # A moving or unstable initial pose cannot define a neutral baseline.
            self._neutral_started_ms = timestamp_ms
            self._neutral_samples = [rotation.copy()]
            return
        self._neutral_samples.append(rotation.copy())
        assert self._neutral_started_ms is not None
        elapsed_s = (timestamp_ms - self._neutral_started_ms) / 1000.0
        if elapsed_s < self.neutral_calibration_seconds or len(self._neutral_samples) < 12:
            return
        average = np.mean(np.stack(self._neutral_samples), axis=0)
        left, _, right = np.linalg.svd(average)
        neutral = left @ right
        if np.linalg.det(neutral) < 0:
            left[:, -1] *= -1
            neutral = left @ right
        self.neutral_rotation = tuple(tuple(float(value) for value in row) for row in neutral)
        self._neutral_samples.clear()
        self._neutral_started_ms = None

    @staticmethod
    def _relative_euler_degrees(rotation: Any, neutral_rotation: Any) -> tuple[float, float, float]:
        """Return current orientation relative to neutral in the driver's axes.

        Face and solvePnP rotations map face-model coordinates into camera
        coordinates. Applying the inverse neutral rotation on the left
        (``neutral.T @ current``) expresses movement in the driver's enrolled
        head frame. This keeps yaw and pitch tied to the driver's own neutral
        axes when the camera or natural sitting posture is slightly tilted.
        The preview is not mirrored before pose estimation, so the configured
        yaw signs continue to refer to the raw camera image.
        """
        return HeadPoseEstimator._euler_degrees(neutral_rotation.T @ rotation)

    @staticmethod
    def _euler_degrees(rotation: Any) -> tuple[float, float, float]:
        # Decompose R = Rz(roll) * Ry(yaw) * Rx(pitch). For relative pose,
        # these axes belong to the enrolled neutral head frame. Pitch is head
        # rotation, not vertical image translation or an eye-gaze estimate.
        sy = math.sqrt(float(rotation[0, 0]) ** 2 + float(rotation[1, 0]) ** 2)
        singular = sy < 1e-6
        if not singular:
            x = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
            y = math.atan2(-float(rotation[2, 0]), sy)
            z = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
        else:
            x = math.atan2(-float(rotation[1, 2]), float(rotation[1, 1]))
            y = math.atan2(-float(rotation[2, 0]), sy)
            z = 0.0
        return tuple(math.degrees(value) for value in (y, x, z))

    @staticmethod
    def _unknown(reason: str, error_px: float | None = None) -> HeadPoseObservation:
        return HeadPoseObservation(False, None, None, None, None, None, None, error_px, None, reason)
