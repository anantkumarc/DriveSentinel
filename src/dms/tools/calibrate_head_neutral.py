from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from dms.capture.backend import CaptureBackend
from dms.detectors.face_landmarks import FaceLandmarksDetector
from dms.detectors.head_pose import CameraCalibration, HeadPoseEstimator


def _parse_source(value: str) -> int | str:
    return int(value) if value.isdecimal() else value


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _run(args: argparse.Namespace) -> int:
    import cv2
    import numpy as np

    output = Path(args.output).expanduser()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite neutral reference: {output}")
    calibration_path = Path(args.camera_calibration).expanduser()
    calibration = CameraCalibration.load(calibration_path)
    detector = FaceLandmarksDetector(args.face_model)
    pose = HeadPoseEstimator(calibration)
    capture = CaptureBackend(_parse_source(args.source), args.width, args.height)
    capture.start()
    rotations = []
    last_sequence = 0
    origin_s = None
    last_timestamp_ms = -1
    start = time.monotonic()
    print("Sit upright in your ordinary neutral viewing posture. Press s to begin capture; q cancels.")
    began = False
    try:
        while True:
            packet = capture.read_newer(last_sequence, timeout_s=0.2)
            if packet is None:
                snapshot = capture.snapshot()
                if snapshot.status.value in {"eof", "unavailable", "failed", "stopped"}:
                    raise RuntimeError(snapshot.error or snapshot.status.value)
                continue
            last_sequence = packet.sequence
            if origin_s is None:
                origin_s = packet.source_time_s
            timestamp_ms = max(int(round((packet.source_time_s - origin_s) * 1000)), last_timestamp_ms + 1)
            last_timestamp_ms = timestamp_ms
            face = detector.detect(packet.image, timestamp_ms)
            result = pose.estimate(face)
            view = packet.image.copy()
            text = "Pose: unavailable" if not result.valid else f"Pose observed; samples {len(rotations)}"
            cv2.putText(view, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 0) if result.valid else (0, 180, 255), 2)
            if result.reason:
                cv2.putText(view, result.reason, (12, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 180, 255), 2)
            cv2.imshow("Neutral Head Pose Calibration", view)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                return 1
            if key == ord("s") and not began:
                began = True
                start = time.monotonic()
                print(f"Collecting for {args.seconds:.1f}s. Keep your posture steady.")
            if began and result.valid and result.rotation_matrix is not None:
                rotations.append(np.asarray(result.rotation_matrix, dtype=np.float64))
            if began and time.monotonic() - start >= args.seconds:
                break
    finally:
        capture.stop()
        detector.close()
        cv2.destroyAllWindows()

    if not began or len(rotations) < args.min_frames:
        raise RuntimeError(f"need at least {args.min_frames} valid pose frames; got {len(rotations)}")
    mean_matrix = np.mean(np.stack(rotations), axis=0)
    u, _, vt = np.linalg.svd(mean_matrix)
    neutral = u @ vt
    if np.linalg.det(neutral) < 0:
        u[:, -1] *= -1
        neutral = u @ vt
    result = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "neutral_rotation_matrix": neutral.tolist(),
        "camera_calibration_sha256": _sha256(calibration_path),
        "pose_frames": len(rotations),
        "neutral_posture": "user-declared upright posture during a stationary setup",
        "warning": "Reference angles and detector signs still require movement validation.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(f"Saved neutral reference: {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a user-declared neutral head-pose reference")
    parser.add_argument("--source", default="0")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--min-frames", type=int, default=10)
    parser.add_argument("--camera-calibration", required=True)
    parser.add_argument("--face-model", default="models/face_landmarker.task")
    parser.add_argument("--output", default="configs/head_pose_neutral.json")
    args = parser.parse_args()
    if args.seconds <= 0 or args.min_frames < 1:
        parser.error("seconds and minimum frames must be positive")
    try:
        return _run(args)
    except (FileNotFoundError, FileExistsError, OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"head neutral calibration: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
