"""Bounded latest-frame capture for camera devices and local video files."""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import TypeAlias

from dms.capture.packets import CaptureInfo, CaptureSnapshot, CaptureStatus, FramePacket

CaptureSource: TypeAlias = int | str
_TERMINAL = {
    CaptureStatus.EOF,
    CaptureStatus.UNAVAILABLE,
    CaptureStatus.FAILED,
    CaptureStatus.STOPPED,
}


class CaptureBackend:
    """Capture in a daemon thread and retain at most one unconsumed frame.

    A video file is paced using decoder timestamps. If those timestamps are
    missing or duplicated, a constant-frame-rate estimate is used. A live
    camera read failure is reported as unavailable; reconnect is manual in
    this first milestone.
    """

    def __init__(
        self,
        source: CaptureSource,
        width: int = 640,
        height: int = 480,
    ) -> None:
        if isinstance(source, bool) or not isinstance(source, (int, str)):
            raise TypeError("source must be a camera index or a video path")
        if width <= 0 or height <= 0:
            raise ValueError("requested capture width and height must be positive")

        self.source = source
        self.width = width
        self.height = height
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._latest: FramePacket | None = None
        self._status = CaptureStatus.STARTING
        self._info: CaptureInfo | None = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_live_camera(self) -> bool:
        return isinstance(self.source, int)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("capture backend can only be started once")
        self._thread = threading.Thread(
            target=self._run,
            name="dms-capture",
            daemon=True,
        )
        self._thread.start()

    def snapshot(self) -> CaptureSnapshot:
        with self._condition:
            return CaptureSnapshot(self._status, self._info, self._error)

    def read_newer(self, after_sequence: int, timeout_s: float = 0.2) -> FramePacket | None:
        """Wait for a newer packet; never return the same frame twice."""
        deadline = time.monotonic() + max(timeout_s, 0.0)
        with self._condition:
            while True:
                if self._latest is not None and self._latest.sequence > after_sequence:
                    return self._latest
                if self._status in _TERMINAL:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def stop(self, join_timeout_s: float = 1.0) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(max(0.0, join_timeout_s))

    def _set_status(
        self,
        status: CaptureStatus,
        *,
        info: CaptureInfo | None = None,
        error: str | None = None,
    ) -> None:
        with self._condition:
            self._status = status
            if info is not None:
                self._info = info
            if error is not None:
                self._error = error
            self._condition.notify_all()

    def _run(self) -> None:
        cap = None
        sequence = 0
        previous_source_time = -1.0
        file_source = not self.is_live_camera
        wall_origin: float | None = None
        media_origin: float | None = None
        try:
            import cv2

            if file_source and not Path(str(self.source)).is_file():
                self._set_status(
                    CaptureStatus.FAILED,
                    error=f"video file does not exist: {self.source}",
                )
                return

            cap = cv2.VideoCapture(self.source)
            if self.is_live_camera:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                # Best effort only. Not all capture backends support this.
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                label = "camera" if self.is_live_camera else "video source"
                self._set_status(
                    CaptureStatus.UNAVAILABLE,
                    error=f"could not open {label}: {self.source}",
                )
                return

            raw_fps = float(cap.get(cv2.CAP_PROP_FPS))
            fps_ok = math.isfinite(raw_fps) and 0.1 <= raw_fps <= 240.0
            fps = raw_fps if fps_ok else None
            try:
                backend = cap.getBackendName()
            except Exception:
                backend = "unknown"
            width = int(round(cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
            height = int(round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            info = CaptureInfo(
                source_kind="camera" if self.is_live_camera else "video",
                source_id=f"camera_index_{self.source}" if self.is_live_camera else "local_video_file",
                width=width,
                height=height,
                reported_fps=fps,
                backend=backend,
                fps_is_reported=fps is not None,
            )
            self._set_status(CaptureStatus.RUNNING, info=info)

            while not self._stop.is_set():
                ok, image = cap.read()
                captured_ns = time.monotonic_ns()
                if not ok or image is None:
                    end_state = CaptureStatus.EOF if file_source else CaptureStatus.UNAVAILABLE
                    message = (
                        "end of video file"
                        if file_source
                        else "camera read failed; restart the app after checking permissions or connection"
                    )
                    self._set_status(end_state, error=message)
                    break
                if getattr(image, "ndim", None) != 3 or image.shape[2] != 3:
                    self._set_status(
                        CaptureStatus.UNAVAILABLE,
                        error="capture returned an invalid image shape",
                    )
                    break

                sequence += 1
                if file_source:
                    frame_step = 1.0 / (fps or 30.0)
                    media_time = float(cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
                    if not math.isfinite(media_time) or media_time <= previous_source_time:
                        media_time = previous_source_time + frame_step
                    source_time_s = media_time
                    previous_source_time = source_time_s
                    if media_origin is None:
                        media_origin = source_time_s
                        wall_origin = time.monotonic()
                else:
                    source_time_s = captured_ns / 1_000_000_000.0

                image.setflags(write=False)
                packet = FramePacket(
                    sequence=sequence,
                    source_time_s=source_time_s,
                    captured_monotonic_ns=captured_ns,
                    image=image,
                )
                with self._condition:
                    self._latest = packet
                    self._status = CaptureStatus.RUNNING
                    self._condition.notify_all()

                if file_source and wall_origin is not None and media_origin is not None:
                    due = wall_origin + max(0.0, source_time_s - media_origin)
                    self._stop.wait(max(0.0, due - time.monotonic()))
        except Exception as exc:
            if not self._stop.is_set():
                self._set_status(CaptureStatus.FAILED, error=f"capture error: {exc}")
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception as exc:
                    self._set_status(CaptureStatus.FAILED, error=f"capture cleanup error: {exc}")
            snapshot = self.snapshot()
            if snapshot.status in {CaptureStatus.STARTING, CaptureStatus.RUNNING}:
                self._set_status(CaptureStatus.STOPPED)
