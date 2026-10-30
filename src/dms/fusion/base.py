from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class RiskState(str, Enum):
    UNKNOWN = "unknown"
    NO_TRIGGER = "no_trigger_supported"
    ELEVATED_CANDIDATE = "elevated_candidate"
    HIGH_CANDIDATE = "high_candidate"


@dataclass(frozen=True, slots=True)
class Evidence:
    name: str
    value: float | None
    valid: bool
    quality: float = 1.0
    age_s: float = 0.0
    validated: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RiskDecision:
    channel: str
    state: RiskState
    score: float | None
    reasons: tuple[str, ...]
    supporting: tuple[str, ...]
    unavailable: tuple[str, ...]
    strategy: str
    critical_bypass: bool = False


def supported_signals(
    evidence: Mapping[str, Evidence],
    *,
    max_age_s: float = 1.0,
) -> tuple[dict[str, Evidence], tuple[str, ...]]:
    supported: dict[str, Evidence] = {}
    unavailable: list[str] = []
    for name, item in evidence.items():
        if not item.valid or item.value is None:
            unavailable.append(name)
        elif item.quality <= 0 or item.age_s < 0 or item.age_s > max_age_s:
            unavailable.append(name)
        else:
            supported[name] = item
    return supported, tuple(unavailable)


def critical_decision(channel: str, event_id: str | None, strategy: str) -> RiskDecision:
    reason = "critical_eye_closure" if event_id is None else f"critical_eye_closure:{event_id}"
    return RiskDecision(
        channel,
        RiskState.HIGH_CANDIDATE,
        1.0,
        (reason,),
        ("critical_eye_closure",),
        (),
        strategy,
        True,
    )
