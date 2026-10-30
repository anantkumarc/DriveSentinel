"""Independent, monotonic timers for sustained head-direction cues."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


HEAD_TURN_THRESHOLD_SECONDS = 5.0
DOWNWARD_HEAD_THRESHOLD_SECONDS = 3.0


class HeadDirection(str, Enum):
    FORWARD = "forward"
    LEFT = "left"
    RIGHT = "right"
    DOWN = "down"
    LEFT_DOWN = "left_down"
    RIGHT_DOWN = "right_down"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HeadDistractionSnapshot:
    valid: bool
    direction: HeadDirection
    yaw_deg: float | None
    pitch_deg: float | None
    roll_deg: float | None
    left_duration_s: float
    right_duration_s: float
    downward_duration_s: float
    left_active: bool
    right_active: bool
    downward_active: bool
    left_episode_id: str | None
    right_episode_id: str | None
    downward_episode_id: str | None

    @property
    def active(self) -> bool:
        return self.left_active or self.right_active or self.downward_active

    @property
    def active_reasons(self) -> tuple[str, ...]:
        reasons = []
        if self.left_active:
            reasons.append("sustained_left_turn")
        if self.right_active:
            reasons.append("sustained_right_turn")
        if self.downward_active:
            reasons.append("sustained_downward_look")
        return tuple(reasons)


@dataclass(slots=True)
class _ConditionTimer:
    duration_s: float = 0.0
    last_valid_s: float | None = None
    recovery_since_s: float | None = None
    recovery_elapsed_s: float = 0.0
    last_condition: bool | None = None
    active: bool = False
    episode_counter: int = 0
    episode_id: str | None = None


class HeadDistractionTracker:
    """Track left, right, and downward head direction as separate conditions.

    ``now_s`` must come from a monotonic clock. A brief invalid observation
    cannot clear an active warning. An invalid interval longer than
    ``max_sample_gap_s`` breaks a not-yet-triggered candidate so its elapsed
    time is never accumulated across a tracking interruption.
    """

    def __init__(
        self,
        *,
        left_yaw_threshold_deg: float = 20.0,
        right_yaw_threshold_deg: float = 20.0,
        downward_pitch_threshold_deg: float = 18.0,
        head_turn_duration_s: float | None = None,
        downward_duration_s: float | None = None,
        # Compatibility for older callers that used one duration for every cue.
        sustained_duration_s: float | None = None,
        recovery_duration_s: float = 0.5,
        max_sample_gap_s: float = 0.5,
        left_yaw_sign: int = -1,
        right_yaw_sign: int = 1,
        downward_pitch_sign: int = 1,
        left_yaw_recovery_threshold_deg: float | None = None,
        right_yaw_recovery_threshold_deg: float | None = None,
        downward_pitch_recovery_threshold_deg: float | None = None,
        smoothing_time_constant_s: float = 0.15,
    ) -> None:
        thresholds = (left_yaw_threshold_deg, right_yaw_threshold_deg, downward_pitch_threshold_deg)
        if sustained_duration_s is not None:
            # Preserve the old single-duration API when a caller explicitly uses it.
            head_turn_duration_s = sustained_duration_s if head_turn_duration_s is None else head_turn_duration_s
            downward_duration_s = sustained_duration_s if downward_duration_s is None else downward_duration_s
        head_turn_duration_s = (
            HEAD_TURN_THRESHOLD_SECONDS if head_turn_duration_s is None else head_turn_duration_s
        )
        downward_duration_s = (
            DOWNWARD_HEAD_THRESHOLD_SECONDS if downward_duration_s is None else downward_duration_s
        )
        left_yaw_recovery_threshold_deg = (
            left_yaw_threshold_deg * 0.75
            if left_yaw_recovery_threshold_deg is None
            else left_yaw_recovery_threshold_deg
        )
        right_yaw_recovery_threshold_deg = (
            right_yaw_threshold_deg * 0.75
            if right_yaw_recovery_threshold_deg is None
            else right_yaw_recovery_threshold_deg
        )
        downward_pitch_recovery_threshold_deg = (
            downward_pitch_threshold_deg * 0.75
            if downward_pitch_recovery_threshold_deg is None
            else downward_pitch_recovery_threshold_deg
        )
        durations = (head_turn_duration_s, downward_duration_s, max_sample_gap_s)
        if any(not math.isfinite(value) or value <= 0 for value in thresholds + durations):
            raise ValueError("head-distraction thresholds and durations must be finite and positive")
        recovery_thresholds = (
            left_yaw_recovery_threshold_deg,
            right_yaw_recovery_threshold_deg,
            downward_pitch_recovery_threshold_deg,
        )
        if any(
            not math.isfinite(value) or value <= 0 or value >= activation
            for value, activation in zip(recovery_thresholds, thresholds)
        ):
            raise ValueError("recovery thresholds must be positive and lower than their activation thresholds")
        if not math.isfinite(recovery_duration_s) or recovery_duration_s < 0:
            raise ValueError("recovery duration must be finite and nonnegative")
        if not math.isfinite(smoothing_time_constant_s) or smoothing_time_constant_s < 0:
            raise ValueError("angle smoothing time constant must be finite and nonnegative")
        if left_yaw_sign not in {-1, 1} or right_yaw_sign not in {-1, 1} or left_yaw_sign == right_yaw_sign:
            raise ValueError("left and right yaw signs must be opposite values of -1 or 1")
        if downward_pitch_sign not in {-1, 1}:
            raise ValueError("downward pitch sign must be -1 or 1")

        self.left_yaw_threshold_deg = left_yaw_threshold_deg
        self.right_yaw_threshold_deg = right_yaw_threshold_deg
        self.downward_pitch_threshold_deg = downward_pitch_threshold_deg
        self.left_yaw_recovery_threshold_deg = left_yaw_recovery_threshold_deg
        self.right_yaw_recovery_threshold_deg = right_yaw_recovery_threshold_deg
        self.downward_pitch_recovery_threshold_deg = downward_pitch_recovery_threshold_deg
        self.head_turn_duration_s = head_turn_duration_s
        self.downward_duration_s = downward_duration_s
        self.recovery_duration_s = recovery_duration_s
        self.max_sample_gap_s = max_sample_gap_s
        self.smoothing_time_constant_s = smoothing_time_constant_s
        self.left_yaw_sign = left_yaw_sign
        self.right_yaw_sign = right_yaw_sign
        self.downward_pitch_sign = downward_pitch_sign
        self._left = _ConditionTimer()
        self._right = _ConditionTimer()
        self._downward = _ConditionTimer()
        self._last_update_s: float | None = None
        self._last_angle_s: float | None = None
        self._smoothed_yaw_deg: float | None = None
        self._smoothed_pitch_deg: float | None = None

    @property
    def sustained_duration_s(self) -> float:
        """Legacy display/API alias for the head-turn duration."""
        return self.head_turn_duration_s

    def reset(self, now_s: float) -> None:
        """Clear active/candidate timers after an operator re-baselines pose."""
        if not math.isfinite(now_s):
            raise ValueError("head-distraction reset timestamp must be finite")
        counters = (self._left.episode_counter, self._right.episode_counter, self._downward.episode_counter)
        self._left = _ConditionTimer(episode_counter=counters[0])
        self._right = _ConditionTimer(episode_counter=counters[1])
        self._downward = _ConditionTimer(episode_counter=counters[2])
        self._last_update_s = now_s
        self._last_angle_s = None
        self._smoothed_yaw_deg = None
        self._smoothed_pitch_deg = None

    def update(
        self,
        now_s: float,
        *,
        yaw_deg: float | None,
        pitch_deg: float | None,
        roll_deg: float | None,
        valid: bool,
    ) -> HeadDistractionSnapshot:
        if not math.isfinite(now_s):
            raise ValueError("head-distraction timestamp must be finite")
        if self._last_update_s is not None and now_s <= self._last_update_s:
            raise ValueError("head-distraction timestamps must increase")
        self._last_update_s = now_s

        valid = bool(valid) and self._finite(yaw_deg) and self._finite(pitch_deg)
        if not valid:
            for timer in (self._left, self._right, self._downward):
                # Unknown is not evidence of recovery. It does, however, break
                # an untriggered candidate if the next valid sample is too late.
                timer.recovery_since_s = None
                timer.recovery_elapsed_s = 0.0
                timer.last_condition = None
            return self._snapshot(now_s, False, HeadDirection.UNKNOWN, None, None, None)

        assert yaw_deg is not None and pitch_deg is not None
        measured_yaw_deg = yaw_deg
        measured_pitch_deg = pitch_deg
        yaw_deg, pitch_deg = self._smooth_angles(now_s, yaw_deg, pitch_deg)
        left_value = yaw_deg * self.left_yaw_sign
        right_value = yaw_deg * self.right_yaw_sign
        down_value = pitch_deg * self.downward_pitch_sign
        left_condition = self._condition(
            self._left,
            left_value,
            measured_yaw_deg * self.left_yaw_sign,
            self.left_yaw_threshold_deg,
            self.left_yaw_recovery_threshold_deg,
        )
        right_condition = self._condition(
            self._right,
            right_value,
            measured_yaw_deg * self.right_yaw_sign,
            self.right_yaw_threshold_deg,
            self.right_yaw_recovery_threshold_deg,
        )
        down_condition = self._condition(
            self._downward,
            down_value,
            measured_pitch_deg * self.downward_pitch_sign,
            self.downward_pitch_threshold_deg,
            self.downward_pitch_recovery_threshold_deg,
        )

        self._update_condition(self._left, left_condition, now_s, "left", self.head_turn_duration_s)
        self._update_condition(self._right, right_condition, now_s, "right", self.head_turn_duration_s)
        self._update_condition(self._downward, down_condition, now_s, "down", self.downward_duration_s)

        direction = self._direction(left_condition, right_condition, down_condition)
        return self._snapshot(now_s, True, direction, yaw_deg, pitch_deg, roll_deg)

    def _smooth_angles(self, now_s: float, yaw_deg: float, pitch_deg: float) -> tuple[float, float]:
        gap_s = None if self._last_angle_s is None else now_s - self._last_angle_s
        if (
            self._smoothed_yaw_deg is None
            or self._smoothed_pitch_deg is None
            or gap_s is None
            or gap_s > self.max_sample_gap_s
            or self.smoothing_time_constant_s == 0.0
        ):
            self._smoothed_yaw_deg = yaw_deg
            self._smoothed_pitch_deg = pitch_deg
        else:
            alpha = 1.0 - math.exp(-gap_s / self.smoothing_time_constant_s)
            self._smoothed_yaw_deg += alpha * (yaw_deg - self._smoothed_yaw_deg)
            self._smoothed_pitch_deg += alpha * (pitch_deg - self._smoothed_pitch_deg)
        self._last_angle_s = now_s
        return self._smoothed_yaw_deg, self._smoothed_pitch_deg

    @staticmethod
    def _condition(
        timer: _ConditionTimer,
        smoothed_value_deg: float,
        raw_value_deg: float,
        activation_threshold_deg: float,
        recovery_threshold_deg: float,
    ) -> bool:
        # Once a candidate starts, retain it through the hysteresis band. This
        # prevents small angle noise from repeatedly resetting its independent timer.
        # Recovery uses the raw measurement so a clean return to neutral silences
        # an active warning on this frame instead of waiting for the filter tail.
        if timer.active or timer.duration_s > 0.0 or timer.last_condition is True:
            return raw_value_deg >= recovery_threshold_deg
        return smoothed_value_deg >= activation_threshold_deg

    def _update_condition(
        self,
        timer: _ConditionTimer,
        condition: bool,
        now_s: float,
        label: str,
        activation_duration_s: float,
    ) -> None:
        gap_s = None if timer.last_valid_s is None else now_s - timer.last_valid_s
        continuous = (
            gap_s is not None
            and gap_s <= self.max_sample_gap_s
            and timer.last_condition is not None
        )
        if gap_s is not None and gap_s > self.max_sample_gap_s:
            timer.recovery_since_s = None
            timer.recovery_elapsed_s = 0.0
            if not timer.active:
                timer.duration_s = 0.0
        timer.last_valid_s = now_s

        if condition:
            timer.recovery_since_s = None
            timer.recovery_elapsed_s = 0.0
            if continuous and timer.last_condition is True and gap_s is not None:
                timer.duration_s += gap_s
            elif not timer.active and (
                timer.duration_s == 0.0 or gap_s is None or gap_s > self.max_sample_gap_s
            ):
                timer.duration_s = 0.0
            if not timer.active and timer.duration_s > activation_duration_s:
                timer.active = True
                timer.episode_counter += 1
                timer.episode_id = f"head-{label}-{timer.episode_counter:06d}"
            timer.last_condition = True
            return

        if not timer.active:
            timer.duration_s = 0.0
            timer.last_condition = False
            return
        if not continuous or timer.last_condition is not False or timer.recovery_since_s is None:
            timer.recovery_since_s = now_s
            timer.recovery_elapsed_s = 0.0
        elif gap_s is not None:
            timer.recovery_elapsed_s += gap_s
        timer.last_condition = False
        if timer.recovery_elapsed_s >= self.recovery_duration_s:
            timer.active = False
            timer.duration_s = 0.0
            timer.recovery_since_s = None
            timer.recovery_elapsed_s = 0.0
            timer.episode_id = None

    def _snapshot(
        self,
        now_s: float,
        valid: bool,
        direction: HeadDirection,
        yaw_deg: float | None,
        pitch_deg: float | None,
        roll_deg: float | None,
    ) -> HeadDistractionSnapshot:
        return HeadDistractionSnapshot(
            valid=valid,
            direction=direction,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
            roll_deg=roll_deg,
            left_duration_s=self._left.duration_s,
            right_duration_s=self._right.duration_s,
            downward_duration_s=self._downward.duration_s,
            left_active=self._left.active,
            right_active=self._right.active,
            downward_active=self._downward.active,
            left_episode_id=self._left.episode_id,
            right_episode_id=self._right.episode_id,
            downward_episode_id=self._downward.episode_id,
        )

    @staticmethod
    def _finite(value: float | None) -> bool:
        return value is not None and math.isfinite(value)

    @staticmethod
    def _direction(left: bool, right: bool, down: bool) -> HeadDirection:
        if left and down:
            return HeadDirection.LEFT_DOWN
        if right and down:
            return HeadDirection.RIGHT_DOWN
        if left:
            return HeadDirection.LEFT
        if right:
            return HeadDirection.RIGHT
        if down:
            return HeadDirection.DOWN
        return HeadDirection.FORWARD
