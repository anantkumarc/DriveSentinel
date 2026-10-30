from __future__ import annotations

import argparse
import json
import math
import time
from typing import Any

from dms.capture.backend import CaptureBackend, CaptureSource
from dms.health.watchdog import CaptureWatchdog


def _parse_source(value: str) -> CaptureSource:
    return int(value) if value.isdecimal() else value


def _main(args: argparse.Namespace) -> int:
    capture = CaptureBackend(_parse_source(args.source), args.width, args.height)
    capture.start()
    first = capture.read_newer(0, timeout_s=min(args.seconds, 5.0))
    snapshot = capture.snapshot()
    if first is None:
        capture.stop()
        raise RuntimeError(snapshot.error or f"capture status: {snapshot.status.value}")

    info: Any = snapshot.info
    watchdog = CaptureWatchdog(unavailable_after_s=1.0)
    watchdog.observe_packet(first)
    started = time.monotonic()
    first_time = first.source_time_s
    last_time = first_time
    previous_sequence = first.sequence
    frames = 1
    skipped = 0
    while time.monotonic() - started < args.seconds:
        packet = capture.read_newer(previous_sequence, timeout_s=0.2)
        if packet is None:
            current = capture.snapshot()
            if current.status.value in {"eof", "unavailable", "failed", "stopped"}:
                break
            health, _ = watchdog.health(current)
            if health == "unavailable":
                break
            continue
        skipped += max(0, packet.sequence - previous_sequence - 1)
        previous_sequence = packet.sequence
        last_time = packet.source_time_s
        watchdog.observe_packet(packet)
        frames += 1

    end_time = time.monotonic()
    final = capture.snapshot()
    capture.stop()
    health, last_frame_age_s = watchdog.health(final)
    if final.status.value == "eof":
        health = "eof"
    elapsed = max(1e-9, end_time - started)
    summary = {
        "status": final.status.value,
        "health": health,
        "error": final.error,
        "source_kind": info.source_kind if info else None,
        "source_id": info.source_id if info else None,
        "width": info.width if info else None,
        "height": info.height if info else None,
        "reported_fps": info.reported_fps if info else None,
        "fps_is_reported": info.fps_is_reported if info else False,
        "backend": info.backend if info else None,
        "probe_duration_s": round(elapsed, 3),
        "frames_observed_after_first": max(0, frames - 1),
        "measured_observed_fps": round(max(0, frames - 1) / elapsed, 3),
        "skipped_capture_frames": skipped,
        "source_timestamp_span_s": round(max(0.0, last_time - first_time), 3),
        "last_frame_age_s": None if last_frame_age_s is None else round(last_frame_age_s, 3),
        "first_source_time_s": first_time,
        "last_sequence": previous_sequence,
    }
    print(json.dumps(summary, indent=2))
    return 0 if health in {"available", "eof"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a camera or video capture mode")
    parser.add_argument("--source", default="0", help="camera index or local video path")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be positive")
    try:
        return _main(args)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(2, f"camera probe: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
