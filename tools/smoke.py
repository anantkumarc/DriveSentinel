from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect runtime imports and optionally load the model")
    parser.add_argument("--config", default="configs/full.yaml")
    parser.add_argument("--require-model", action="store_true", help="fail when model asset is absent")
    args = parser.parse_args()
    config_path = Path(args.config).expanduser().resolve()
    try:
        import cv2
        import mediapipe as mp
        import yaml
    except ImportError as exc:
        print(json.dumps({"error": str(exc), "python": sys.version.split()[0]}, indent=2))
        return 2

    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    project_root = config_path.parent.parent
    model_path = Path(config["face"]["model"])
    if not model_path.is_absolute():
        model_path = project_root / model_path
    model_exists = model_path.is_file()
    model_load_ok = None
    model_error = None
    model_sha256 = None
    if model_exists:
        digest = hashlib.sha256()
        with model_path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        model_sha256 = digest.hexdigest()
        try:
            from dms.detectors.eyes import FaceLandmarkerEyes

            detector = FaceLandmarkerEyes(
                model_path,
                ear_closed_threshold=float(config["eyes"]["ear_closed_threshold"]),
                min_eye_width_px=float(config["face"]["min_eye_width_px"]),
                min_face_detection_confidence=float(config["face"]["min_face_detection_confidence"]),
            )
            detector.close()
            model_load_ok = True
        except Exception as exc:
            model_load_ok = False
            model_error = str(exc)

    distributions = [
        distribution.metadata.get("Name", "")
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name", "").lower().startswith("opencv-")
    ]
    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "mediapipe_distribution": _version("mediapipe"),
        "mediapipe_tasks_api": hasattr(mp, "tasks") and hasattr(mp.tasks, "vision"),
        "opencv_version": cv2.__version__,
        "opencv_distributions": distributions,
        "pyyaml_version": _version("PyYAML"),
        "model_path": str(model_path),
        "model_exists": model_exists,
        "model_sha256": model_sha256,
        "model_load_ok": model_load_ok,
        "model_error": model_error,
        "python_supported_by_project": (3, 11) <= sys.version_info[:2] < (3, 15),
    }
    print(json.dumps(report, indent=2))
    if not report["mediapipe_tasks_api"] or len(distributions) != 1:
        return 1
    if model_load_ok is False:
        return 1
    if args.require_model and not model_exists:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
