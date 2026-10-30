from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

from dms.fusion.base import RiskDecision, RiskState


class AlertLevel(IntEnum):
    NONE = 0
    ELEVATED = 1
    HIGH = 2
    CRITICAL = 3


@dataclass(frozen=True, slots=True)
class AlertState:
    channel: str
    level: AlertLevel
    episode_id: str | None
    reasons: tuple[str, ...]
    evidence_state: RiskState
    evidence_unknown: bool
    notify: bool
    notification_reason: str | None


class AlertStateMachine:
    """Persistence, priority, recovery, cooldown, and episode deduplication."""

    def __init__(
        self,
        channel: str,
        *,
        entry_persistence_s: float = 0.5,
        recovery_persistence_s: float = 1.0,
        repeat_cooldown_s: float = 8.0,
        max_sample_gap_s: float = 0.5,
        repeat_notifications: bool = True,
    ) -> None:
        durations = (entry_persistence_s, recovery_persistence_s, repeat_cooldown_s)
        if any(not math.isfinite(value) or value < 0 for value in durations):
            raise ValueError("alert durations must be nonnegative")
        if not math.isfinite(max_sample_gap_s) or max_sample_gap_s <= 0:
            raise ValueError("maximum alert sample gap must be positive")
        self.channel = channel
        self.entry_persistence_s = entry_persistence_s
        self.recovery_persistence_s = recovery_persistence_s
        self.repeat_cooldown_s = repeat_cooldown_s
        self.max_sample_gap_s = max_sample_gap_s
        self.repeat_notifications = repeat_notifications
        self.level = AlertLevel.NONE
        self.episode_id: str | None = None
        self._candidate_since_s: float | None = None
        self._candidate_level = AlertLevel.NONE
        self._recovery_since_s: float | None = None
        self._last_notification_s: float | None = None
        self._last_time_s: float | None = None
        self._episode_counter = 0
        self._reasons: tuple[str, ...] = ()

    def update(
        self,
        decision: RiskDecision,
        timestamp_s: float,
        *,
        entry_persistence_override_s: float | None = None,
        recovery_persistence_override_s: float | None = None,
    ) -> AlertState:
        if not math.isfinite(timestamp_s):
            raise ValueError("alert timestamp must be finite")
        if decision.channel != self.channel:
            raise ValueError(f"expected {self.channel} decision, got {decision.channel}")
        if entry_persistence_override_s is not None and (
            not math.isfinite(entry_persistence_override_s) or entry_persistence_override_s < 0
        ):
            raise ValueError("entry persistence override must be finite and nonnegative")
        if recovery_persistence_override_s is not None and (
            not math.isfinite(recovery_persistence_override_s) or recovery_persistence_override_s < 0
        ):
            raise ValueError("recovery persistence override must be finite and nonnegative")
        if self._last_time_s is not None:
            if timestamp_s <= self._last_time_s:
                raise ValueError("alert timestamps must increase")
            if timestamp_s - self._last_time_s > self.max_sample_gap_s:
                # A positive or recovering interval cannot be assumed to have
                # persisted while observations were absent.
                self._candidate_since_s = None
                self._candidate_level = AlertLevel.NONE
                self._recovery_since_s = None
        self._last_time_s = timestamp_s
        notify = False
        notify_reason = None
        unknown = decision.state is RiskState.UNKNOWN

        # Missing evidence is not recovery. Keep the active alert and episode,
        # while allowing UI to show current monitoring uncertainty.
        if unknown:
            self._candidate_since_s = None
            self._candidate_level = AlertLevel.NONE
            self._recovery_since_s = None
            return self._snapshot(decision, evidence_unknown=True)

        desired = self._desired_level(decision.state, critical=decision.critical_bypass)
        critical = desired is AlertLevel.CRITICAL

        if desired is AlertLevel.NONE:
            self._candidate_since_s = None
            self._candidate_level = AlertLevel.NONE
            if self.level is not AlertLevel.NONE:
                if self._recovery_since_s is None:
                    self._recovery_since_s = timestamp_s
                recovery_persistence_s = (
                    self.recovery_persistence_s
                    if recovery_persistence_override_s is None
                    else recovery_persistence_override_s
                )
                if timestamp_s - self._recovery_since_s >= recovery_persistence_s:
                    self.level = AlertLevel.NONE
                    self.episode_id = None
                    self._reasons = ()
                    self._recovery_since_s = None
            return self._snapshot(decision, evidence_unknown=False)

        self._recovery_since_s = None
        if self.level > desired and desired is not AlertLevel.NONE:
            # Priority is monotonic within an episode; weaker positives cannot
            # demote a current alert before a valid recovery.
            desired = self.level
        if desired > self.level:
            entry_persistence_s = (
                self.entry_persistence_s
                if entry_persistence_override_s is None
                else entry_persistence_override_s
            )
            if critical or entry_persistence_s == 0:
                notify_reason = "critical_bypass" if critical else "entry_transition"
                notify = True
                self._activate(desired, decision, timestamp_s)
            else:
                if self._candidate_level != desired:
                    self._candidate_level = desired
                    self._candidate_since_s = timestamp_s
                if self._candidate_since_s is not None and timestamp_s - self._candidate_since_s >= entry_persistence_s:
                    notify_reason = "entry_persistence"
                    notify = True
                    self._activate(desired, decision, timestamp_s)
        else:
            self._candidate_since_s = None
            self._candidate_level = AlertLevel.NONE
            if desired == self.level:
                self._reasons = decision.reasons
                if self.repeat_notifications and (
                    self._last_notification_s is None
                    or timestamp_s - self._last_notification_s >= self.repeat_cooldown_s
                ):
                    notify = True
                    notify_reason = "repeat_cooldown"

        if notify:
            self._last_notification_s = timestamp_s
        return self._snapshot(decision, evidence_unknown=False, notify=notify, notification_reason=notify_reason)

    def _activate(self, level: AlertLevel, decision: RiskDecision, timestamp_s: float) -> None:
        if self.level is AlertLevel.NONE:
            self._episode_counter += 1
            self.episode_id = f"{self.channel}-{self._episode_counter:06d}"
        self.level = level
        self._reasons = decision.reasons
        self._candidate_since_s = None
        self._candidate_level = AlertLevel.NONE
        self._last_notification_s = timestamp_s

    @staticmethod
    def _desired_level(state: RiskState, *, critical: bool = False) -> AlertLevel:
        if critical and state is RiskState.HIGH_CANDIDATE:
            return AlertLevel.CRITICAL
        if state is RiskState.HIGH_CANDIDATE:
            return AlertLevel.HIGH
        if state is RiskState.ELEVATED_CANDIDATE:
            return AlertLevel.ELEVATED
        return AlertLevel.NONE

    def _snapshot(
        self,
        decision: RiskDecision,
        *,
        evidence_unknown: bool,
        notify: bool = False,
        notification_reason: str | None = None,
    ) -> AlertState:
        return AlertState(
            self.channel,
            self.level,
            self.episode_id,
            self._reasons or decision.reasons,
            decision.state,
            evidence_unknown,
            notify,
            notification_reason,
        )
