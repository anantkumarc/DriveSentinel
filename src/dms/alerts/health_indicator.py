from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HealthIndicator:
    capture: str
    face: str
    eyes: str
    mouth: str
    pose: str
    phone: str
    hands: str
    audio: str
