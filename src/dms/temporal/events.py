from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemporalEvent:
    event_id: str
    channel: str
    kind: str
    start_s: float
    eligible_s: float | None
    end_s: float | None
    status: str
    reason: str


class EventIds:
    """Stable in-run IDs for event streams and alert episodes."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self._next = 0

    def new(self) -> str:
        self._next += 1
        return f"{self.prefix}-{self._next:06d}"
