from dataclasses import dataclass
from enum import Enum
from typing import Any


class CaptureStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    EOF = "eof"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class FramePacket:
    sequence: int
    source_time_s: float
    captured_monotonic_ns: int
    image: Any


@dataclass(frozen=True, slots=True)
class CaptureInfo:
    source_kind: str
    source_id: str
    width: int
    height: int
    reported_fps: float | None
    backend: str
    fps_is_reported: bool


@dataclass(frozen=True, slots=True)
class CaptureSnapshot:
    status: CaptureStatus
    info: CaptureInfo | None
    error: str | None
