from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

from dms.capture.backend import CaptureBackend


def _parse_source(value: str) -> int | str:
    return int(value) if value.isdecimal() else value


def _run(args: argparse.Namespace) -> int:
    import cv2
    import numpy as np

    output = Path(args.output).expanduser()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite calibration: {output}")
    capture = CaptureBackend(_parse_source(args.source), args.width, args.height)
    capture.start()
    object_grid = np.zeros((args.pattern_rows * args.pattern_cols, 3), dtype=np.float32)
    object_grid[:, :2] = np.mgrid[0 : args.pattern_cols, 0 : args.pattern_rows].T.reshape(-1, 2)
    object_grid *= args.square_size_mm
    object_points = []
    image_points = []
    last_sequence = 0
    captured_size = None
    print("Show the checkerboard at varied positions and angles; press c to record a valid view, q to finish.")
    try:
        while True:
            packet = capture.read_newer(last_sequence, timeout_s=0.2)
            if packet is None:
                snapshot = capture.snapshot()
                if snapshot.status.value in {"eof", "unavailable", "failed", "stopped"}:
                    break
                continue
            last_sequence = packet.sequence
            gray = cv2.cvtColor(packet.image, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCorners(
                gray,
                (args.pattern_cols, args.pattern_rows),
                cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE,
            )
            view = packet.image.copy()
            if found:
                corners = cv2.cornerSubPix(
                    gray,
                    corners,
                    (11, 11),
                    (-1, -1),
                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001),
                )
                cv2.drawChessboardCorners(view, (args.pattern_cols, args.pattern_rows), corners, found)
                cv2.putText(view, "Checkerboard detected - press c to record", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            else:
                cv2.putText(view, "Checkerboard not detected", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 180, 255), 2)
            cv2.putText(view, f"Views: {len(image_points)} / {args.min_views}", (12, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imshow("Camera Calibration", view)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("c"):
                if not found:
                    print("No checkerboard in this frame; view not recorded.")
                    continue
                size = (gray.shape[1], gray.shape[0])
                if captured_size is not None and size != captured_size:
                    raise ValueError("capture resolution changed during calibration")
                captured_size = size
                object_points.append(object_grid.copy())
                image_points.append(corners)
                print(f"Recorded view {len(image_points)} at {size[0]}x{size[1]}.")
                time.sleep(0.25)
    finally:
        capture.stop()
        cv2.destroyAllWindows()

    if len(image_points) < args.min_views or captured_size is None:
        raise RuntimeError(f"need at least {args.min_views} valid views; got {len(image_points)}")
    rms, matrix, distortion, _, _ = cv2.calibrateCamera(
        object_points,
        image_points,
        captured_size,
        None,
        None,
    )
    if not math.isfinite(float(rms)):
        raise RuntimeError("calibration returned a non-finite reprojection RMS")
    result = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "image_width": captured_size[0],
        "image_height": captured_size[1],
        "camera_matrix": matrix.tolist(),
        "distortion": distortion.reshape(-1).tolist(),
        "reprojection_rms_px": float(rms),
        "checkerboard_inner_corners": [args.pattern_cols, args.pattern_rows],
        "checkerboard_square_size_mm": args.square_size_mm,
        "views": len(image_points),
        "camera_model": "not queried; record the actual device model here",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(f"Saved calibration: {output} (reprojection RMS {rms:.3f} px)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate OpenCV camera intrinsics with a checkerboard")
    parser.add_argument("--source", default="0")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--pattern-cols", type=int, default=9)
    parser.add_argument("--pattern-rows", type=int, default=6)
    parser.add_argument("--square-size-mm", type=float, default=25.0)
    parser.add_argument("--min-views", type=int, default=12)
    parser.add_argument("--output", default="configs/camera_calibration.json")
    args = parser.parse_args()
    if args.pattern_cols < 3 or args.pattern_rows < 3 or args.square_size_mm <= 0 or args.min_views < 3:
        parser.error("pattern dimensions, square size and minimum views must be positive and usable")
    try:
        return _run(args)
    except (FileNotFoundError, FileExistsError, OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"camera calibration: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
