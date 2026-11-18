"""Bounded latest-frame capture for camera devices and local video files."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import TypeAlias
from urllib.parse import urlsplit

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
    Local camera failures are reported as unavailable. Network streams retry
    after failures using bounded FFmpeg open and read timeouts where supported.
    """

    def __init__(
        self,
        source: CaptureSource,
        width: int = 640,
        height: int = 480,
        rotation: int = 0,
    ) -> None:
        if isinstance(source, bool) or not isinstance(source, (int, str)):
            raise TypeError("source must be a camera index or a video path")
        if width <= 0 or height <= 0:
            raise ValueError("requested capture width and height must be positive")

        if rotation not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180, or 270 degrees clockwise")
        self.rotation = rotation
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
        return isinstance(self.source, int) or self.is_network

    @property
    def is_network(self) -> bool:
        return isinstance(self.source, str) and urlsplit(self.source).scheme.lower() in {"http", "https", "rtsp"}

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

    def stop(self, join_timeout_s: float = 3.0) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(max(0.0, join_timeout_s))

    def _open_capture(self, cv2: object):
        """Use open-time FFmpeg timeouts for network streams.

        OpenCV documents these properties for FFmpeg/GStreamer only; they
        cannot be set after a stream has opened.  A backend that does not
        honor them cannot guarantee immediate interruption of a native read.
        """
        if self.is_network:
            return cv2.VideoCapture(
                self.source,
                cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 2500,
                 cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1500],
            )
        return cv2.VideoCapture(self.source)

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

            cap = self._open_capture(cv2)
            if isinstance(self.source, int):
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                # Best effort only. Not all capture backends support this.
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                label = "camera" if self.is_live_camera else "video source"
                if not self.is_network:
                    self._set_status(CaptureStatus.UNAVAILABLE, error=f"could not open {label}: {self.source}")
                    return
                self._set_status(CaptureStatus.STARTING, error="Wi-Fi stream unavailable; retrying")
                while not cap.isOpened() and not self._stop.wait(2.0):
                    cap.release()
                    cap = self._open_capture(cv2)
                if self._stop.is_set():
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
            if self.rotation in (90, 270):
                width, height = height, width
            info = CaptureInfo(
                source_kind="network" if self.is_network else "camera" if self.is_live_camera else "video",
                source_id="network_stream" if self.is_network else f"camera_index_{self.source}" if self.is_live_camera else "local_video_file",
                width=width,
                height=height,
                reported_fps=fps,
                backend=backend,
                fps_is_reported=fps is not None,
            )
            self._set_status(CaptureStatus.RUNNING, info=info)

            while not self._stop.is_set():
                if self.is_network and not cap.isOpened():
                    cap.release()
                    if self._stop.wait(2.0):
                        break
                    cap = self._open_capture(cv2)
                    continue
                ok, image = cap.read()
                captured_ns = time.monotonic_ns()
                if not ok or image is None:
                    if self.is_network:
                        self._set_status(CaptureStatus.STARTING, error="Wi-Fi stream disconnected; retrying")
                        cap.release()
                        if self._stop.wait(2.0):
                            break
                        cap = self._open_capture(cv2)
                        continue
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

                if self.rotation:
                    image = cv2.rotate(image, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[self.rotation])
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
                    # Actual decoded dimensions can differ from reported values
                    # and can change after reconnect or phone rotation.
                    actual_height, actual_width = image.shape[:2]
                    if self._info and (self._info.width, self._info.height) != (actual_width, actual_height):
                        self._info = replace(self._info, width=actual_width, height=actual_height)
                    self._latest = packet
                    self._status = CaptureStatus.RUNNING
                    self._error = None
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
