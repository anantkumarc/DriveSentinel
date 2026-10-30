from __future__ import annotations

import math
import unittest
from pathlib import Path

import numpy as np
import yaml

from dms.detectors.eyes import EyeObservation, EyeState
from dms.detectors.head_pose import HeadPoseEstimator, HeadPoseObservation
from dms.detectors.mouth import MouthCandidateDecision, MouthState
from dms.detectors.phone_interaction import PhoneInteractionObservation
from dms.fusion.base import RiskDecision, RiskState
from dms.fusion.engine import RiskFusionEngine
from dms.alerts.states import AlertLevel, AlertStateMachine
from dms.temporal.head_distraction import HeadDirection, HeadDistractionTracker
from dms.temporal.windows import WindowFeature


class HeadDistractionTrackerTests(unittest.TestCase):
    def make_tracker(self, **overrides: object) -> HeadDistractionTracker:
        options: dict[str, object] = {
            "left_yaw_threshold_deg": 20.0,
            "right_yaw_threshold_deg": 20.0,
            "downward_pitch_threshold_deg": 18.0,
            "head_turn_duration_s": 5.0,
            "downward_duration_s": 3.0,
            "recovery_duration_s": 0.5,
            "max_sample_gap_s": 0.75,
            "left_yaw_sign": -1,
            "right_yaw_sign": 1,
            "downward_pitch_sign": 1,
            "left_yaw_recovery_threshold_deg": 15.0,
            "right_yaw_recovery_threshold_deg": 15.0,
            "downward_pitch_recovery_threshold_deg": 12.0,
            "smoothing_time_constant_s": 0.0,
        }
        options.update(overrides)
        return HeadDistractionTracker(**options)  # type: ignore[arg-type]

    @staticmethod
    def sample(
        tracker: HeadDistractionTracker,
        now_s: float,
        *,
        yaw: float = 0.0,
        pitch: float = 0.0,
        valid: bool = True,
    ):
        return tracker.update(
            now_s,
            yaw_deg=yaw if valid else None,
            pitch_deg=pitch if valid else None,
            roll_deg=1.5 if valid else None,
            valid=valid,
        )

    def hold(
        self,
        tracker: HeadDistractionTracker,
        start_s: float,
        end_s: float,
        *,
        yaw: float = 0.0,
        pitch: float = 0.0,
        step_s: float = 0.25,
    ):
        if tracker._last_update_s is None:
            self.sample(tracker, start_s, yaw=yaw, pitch=pitch)
        current = start_s + step_s
        result = None
        while current < end_s:
            result = self.sample(tracker, current, yaw=yaw, pitch=pitch)
            current += step_s
        return self.sample(tracker, end_s, yaw=yaw, pitch=pitch)

    def test_left_turn_four_seconds_is_not_high_then_more_than_five_is_high(self) -> None:
        tracker = self.make_tracker()
        at_four = self.hold(tracker, 0.0, 4.0, yaw=-25.0)
        self.assertFalse(at_four.left_active)
        at_five = self.hold(tracker, 4.0, 5.0, yaw=-25.0)
        self.assertFalse(at_five.left_active)
        at_five_one = self.sample(tracker, 5.1, yaw=-25.0)
        self.assertTrue(at_five_one.left_active)
        self.assertEqual(at_five_one.active_reasons, ("sustained_left_turn",))

    def test_right_turn_is_tracked_independently(self) -> None:
        tracker = self.make_tracker()
        at_four = self.hold(tracker, 0.0, 4.0, yaw=24.0)
        self.assertFalse(at_four.right_active)
        at_five = self.hold(tracker, 4.0, 5.0, yaw=24.0)
        self.assertFalse(at_five.right_active)
        result = self.sample(tracker, 5.1, yaw=24.0)
        self.assertTrue(result.right_active)
        self.assertFalse(result.left_active)
        self.assertEqual(result.direction, HeadDirection.RIGHT)

    def test_downward_look_two_seconds_is_not_high_then_more_than_three_is_high(self) -> None:
        tracker = self.make_tracker()
        at_two = self.hold(tracker, 0.0, 2.0, pitch=24.0)
        self.assertFalse(at_two.downward_active)
        at_three = self.hold(tracker, 2.0, 3.0, pitch=24.0)
        self.assertFalse(at_three.downward_active)
        at_three_one = self.sample(tracker, 3.1, pitch=24.0)
        self.assertTrue(at_three_one.downward_active)
        self.assertFalse(at_three_one.left_active)
        self.assertFalse(at_three_one.right_active)

    def test_looking_up_never_counts_as_downward_head_movement(self) -> None:
        tracker = self.make_tracker()
        result = self.hold(tracker, 0.0, 4.0, pitch=-25.0)
        self.assertFalse(result.downward_active)
        self.assertEqual(result.downward_duration_s, 0.0)

    def test_forward_return_before_threshold_resets_candidate(self) -> None:
        tracker = self.make_tracker()
        self.hold(tracker, 0.0, 2.0, yaw=-24.0)
        self.sample(tracker, 2.1, yaw=0.0)
        result = self.hold(tracker, 2.1, 7.0, yaw=-24.0)
        self.assertFalse(result.left_active)
        self.assertLess(result.left_duration_s, 5.0)
        result = self.sample(tracker, 7.36, yaw=-24.0)
        self.assertTrue(result.left_active)

    def test_hysteresis_prevents_noise_from_resetting_candidate_but_normal_pose_resets_it(self) -> None:
        tracker = self.make_tracker(recovery_duration_s=0.0)
        self.hold(tracker, 0.0, 4.0, yaw=-25.0)
        in_hysteresis_band = self.sample(tracker, 4.1, yaw=-18.0)
        self.assertFalse(in_hysteresis_band.left_active)
        self.assertGreater(in_hysteresis_band.left_duration_s, 4.0)
        reset = self.sample(tracker, 4.2, yaw=-10.0)
        self.assertEqual(reset.left_duration_s, 0.0)
        active = self.hold(tracker, 4.2, 9.6, yaw=-25.0)
        self.assertTrue(active.left_active)

    def test_angle_smoothing_reduces_frame_to_frame_noise(self) -> None:
        tracker = self.make_tracker(smoothing_time_constant_s=0.2)
        self.sample(tracker, 0.0, yaw=0.0)
        moved = self.sample(tracker, 0.1, yaw=-30.0)
        self.assertLess(abs(moved.yaw_deg or 0.0), 30.0)
        self.assertFalse(moved.left_active)

    def test_long_interruption_does_not_accumulate_candidate_time(self) -> None:
        tracker = self.make_tracker()
        self.hold(tracker, 0.0, 2.0, yaw=-25.0)
        unknown = self.sample(tracker, 2.1, valid=False)
        self.assertFalse(unknown.left_active)
        resumed = self.sample(tracker, 3.0, yaw=-25.0)
        self.assertFalse(resumed.left_active)
        self.assertEqual(resumed.left_duration_s, 0.0)
        result = self.hold(tracker, 3.0, 7.9, yaw=-25.0)
        self.assertFalse(result.left_active)

    def test_short_invalid_frame_pauses_candidate_instead_of_counting_unknown_time(self) -> None:
        tracker = self.make_tracker()
        self.hold(tracker, 0.0, 2.0, yaw=-25.0)
        self.sample(tracker, 2.1, valid=False)
        resumed = self.sample(tracker, 2.2, yaw=-25.0)
        self.assertAlmostEqual(resumed.left_duration_s, 2.0)
        result = self.hold(tracker, 2.2, 5.1, yaw=-25.0)
        self.assertFalse(result.left_active)
        result = self.sample(tracker, 5.3, yaw=-25.0)
        self.assertTrue(result.left_active)

    def test_missing_landmarks_do_not_recover_an_active_warning(self) -> None:
        tracker = self.make_tracker()
        active = self.hold(tracker, 0.0, 5.1, yaw=-25.0)
        episode_id = active.left_episode_id
        unknown = self.sample(tracker, 5.2, valid=False)
        unknown = self.sample(tracker, 9.0, valid=False)
        self.assertFalse(unknown.valid)
        self.assertTrue(unknown.left_active)
        self.assertEqual(unknown.left_episode_id, episode_id)
        still_active = self.sample(tracker, 9.1, yaw=0.0)
        self.assertTrue(still_active.left_active)
        recovered = self.sample(tracker, 9.7, yaw=0.0)
        self.assertFalse(recovered.left_active)

    def test_left_right_and_down_have_independent_timers_and_single_episode_ids(self) -> None:
        tracker = self.make_tracker(head_turn_duration_s=1.0, downward_duration_s=1.0)
        self.sample(tracker, 0.0, yaw=-25.0, pitch=22.0)
        result = self.hold(tracker, 0.0, 1.1, yaw=-25.0, pitch=22.0)
        self.assertTrue(result.left_active)
        self.assertTrue(result.downward_active)
        self.assertFalse(result.right_active)
        first_id = result.left_episode_id
        result = self.sample(tracker, 1.2, yaw=-25.0, pitch=22.0)
        self.assertEqual(result.left_episode_id, first_id)
        self.assertEqual(result.direction, HeadDirection.LEFT_DOWN)

    def test_simultaneous_turn_and_down_warning_stays_active_until_both_are_clear(self) -> None:
        tracker = self.make_tracker(recovery_duration_s=0.0)
        both_active = self.hold(tracker, 0.0, 5.1, yaw=25.0, pitch=22.0)
        self.assertTrue(both_active.right_active)
        self.assertTrue(both_active.downward_active)

        only_down = self.sample(tracker, 5.2, yaw=0.0, pitch=22.0)
        self.assertFalse(only_down.right_active)
        self.assertTrue(only_down.downward_active)
        self.assertTrue(only_down.active)

        forward = self.sample(tracker, 5.3, yaw=0.0, pitch=0.0)
        self.assertFalse(forward.active)

    def test_episode_id_changes_only_after_recovery_and_reentry(self) -> None:
        tracker = self.make_tracker(head_turn_duration_s=1.0)
        self.sample(tracker, 0.0, yaw=-25.0)
        first = self.hold(tracker, 0.0, 1.1, yaw=-25.0).left_episode_id
        self.sample(tracker, 1.2, yaw=0.0)
        recovered = self.sample(tracker, 1.7, yaw=0.0)
        self.assertFalse(recovered.left_active)
        self.sample(tracker, 1.8, yaw=-25.0)
        second = self.hold(tracker, 1.8, 2.81, yaw=-25.0).left_episode_id
        self.assertNotEqual(first, second)

    def test_direction_sign_mapping_is_configurable_for_mirrored_inputs(self) -> None:
        tracker = self.make_tracker(left_yaw_sign=1, right_yaw_sign=-1, downward_pitch_sign=-1)
        result = self.sample(tracker, 0.0, yaw=25.0, pitch=-21.0)
        self.assertEqual(result.direction, HeadDirection.LEFT_DOWN)

    def test_full_config_downward_sign_and_first_forward_sample_recovery(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "configs" / "full.yaml"
        with config_path.open(encoding="utf-8") as handle:
            options = yaml.safe_load(handle)["head_distraction"]
        tracker = HeadDistractionTracker(
            left_yaw_threshold_deg=float(options["left_yaw_threshold_deg"]),
            right_yaw_threshold_deg=float(options["right_yaw_threshold_deg"]),
            downward_pitch_threshold_deg=float(options["downward_pitch_threshold_deg"]),
            head_turn_duration_s=float(options["head_turn_threshold_seconds"]),
            downward_duration_s=float(options["downward_head_threshold_seconds"]),
            recovery_duration_s=float(options["recovery_duration_seconds"]),
            max_sample_gap_s=float(options["max_sample_gap_seconds"]),
            left_yaw_sign=int(options["left_yaw_sign"]),
            right_yaw_sign=int(options["right_yaw_sign"]),
            downward_pitch_sign=int(options["downward_pitch_sign"]),
            left_yaw_recovery_threshold_deg=float(options["left_yaw_recovery_threshold_deg"]),
            right_yaw_recovery_threshold_deg=float(options["right_yaw_recovery_threshold_deg"]),
            downward_pitch_recovery_threshold_deg=float(options["downward_pitch_recovery_threshold_deg"]),
            smoothing_time_constant_s=float(options["angle_smoothing_time_constant_seconds"]),
        )
        active = self.hold(tracker, 0.0, 3.1, pitch=24.0)
        self.assertTrue(active.downward_active)
        raised = self.sample(tracker, 3.2, pitch=0.0)
        self.assertFalse(raised.downward_active)

    def test_euler_extraction_matches_declared_xyz_axes(self) -> None:
        angle = math.radians(30.0)
        c, s = math.cos(angle), math.sin(angle)
        yaw_matrix = np.asarray(((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c)))
        pitch_matrix = np.asarray(((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c)))
        roll_matrix = np.asarray(((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0)))
        self.assertAlmostEqual(HeadPoseEstimator._euler_degrees(yaw_matrix)[0], 30.0, places=5)
        self.assertAlmostEqual(HeadPoseEstimator._euler_degrees(pitch_matrix)[1], 30.0, places=5)
        self.assertAlmostEqual(HeadPoseEstimator._euler_degrees(roll_matrix)[2], 30.0, places=5)

    def test_relative_pose_is_computed_in_neutral_head_coordinates(self) -> None:
        def rotate_y(degrees: float) -> np.ndarray:
            angle = math.radians(degrees)
            c, s = math.cos(angle), math.sin(angle)
            return np.asarray(((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c)))

        def rotate_x(degrees: float) -> np.ndarray:
            angle = math.radians(degrees)
            c, s = math.cos(angle), math.sin(angle)
            return np.asarray(((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c)))

        neutral = rotate_y(17.0) @ rotate_x(-9.0)
        movement = rotate_y(-32.0) @ rotate_x(21.0)
        current = neutral @ movement
        relative = HeadPoseEstimator._relative_euler_degrees(current, neutral)
        self.assertAlmostEqual(relative[0], -32.0, places=5)
        self.assertAlmostEqual(relative[1], 21.0, places=5)

    def test_sustained_tracker_event_reaches_high_fusion_and_alert(self) -> None:
        tracker = self.make_tracker()
        engine = RiskFusionEngine(
            {
                "method": "rules",
                "channels": {
                    "use_mouth": True,
                    "use_head": False,
                    "use_head_distraction": True,
                    "allow_unvalidated_phone_candidates": True,
                },
                "rules": {"drowsiness": {}, "distraction": {"sustained_head_distraction": 1.0}},
            },
            Path.cwd(),
        )
        machine = AlertStateMachine(
            "distraction",
            entry_persistence_s=0.0,
            recovery_persistence_s=0.0,
            max_sample_gap_s=2.0,
            repeat_notifications=False,
        )

        def evaluate(state):
            return engine.evaluate(
                eye=EyeObservation(EyeState.OPEN, 0.3, 0.3, 0.3, None),
                closure_fraction=0.0,
                perclos=WindowFeature(0.0, 0.0, 60.0, 1.0, True),
                mouth=MouthCandidateDecision(MouthState.CLOSED, 0.0, False, False, False, None, None),
                head_pose=HeadPoseObservation(False, None, None, None, None, None, None, None, None, "not_needed"),
                interaction=PhoneInteractionObservation("no_candidate", False, False, None, None),
                critical_event_id=None,
                phone_age_s=0.0,
                head_distraction_active=state.active,
                head_distraction_valid=state.valid,
                head_distraction_reasons=state.active_reasons,
            )

        four_seconds = self.hold(tracker, 0.0, 4.0, yaw=-25.0)
        before_threshold = evaluate(four_seconds)
        self.assertIsNot(before_threshold.distraction.state, RiskState.HIGH_CANDIDATE)
        no_alert = machine.update(before_threshold.distraction, 4.0)
        self.assertIs(no_alert.level, AlertLevel.NONE)

        exactly_five = self.hold(tracker, 4.0, 5.0, yaw=-25.0)
        at_boundary = evaluate(exactly_five)
        self.assertIsNot(at_boundary.distraction.state, RiskState.HIGH_CANDIDATE)
        self.assertIs(machine.update(at_boundary.distraction, 5.0).level, AlertLevel.NONE)

        sustained = self.sample(tracker, 5.1, yaw=-25.0)
        output = evaluate(sustained)
        self.assertIs(output.distraction.state, RiskState.HIGH_CANDIDATE)
        alert = machine.update(output.distraction, 5.1)
        self.assertIs(alert.level, AlertLevel.HIGH)
        self.assertEqual(alert.reasons, ("sustained_left_turn",))
        sustained_again = self.sample(tracker, 6.2, yaw=-25.0)
        repeated_output = evaluate(sustained_again)
        repeated_alert = machine.update(repeated_output.distraction, 6.2)
        self.assertIs(repeated_alert.level, AlertLevel.HIGH)
        self.assertFalse(repeated_alert.notify)
        self.assertEqual(repeated_alert.episode_id, alert.episode_id)

    def test_hand_phone_candidate_is_diagnostic_only_and_never_activates_alarm(self) -> None:
        engine = RiskFusionEngine(
            {
                "method": "rules",
                "channels": {
                    "use_mouth": True,
                    "use_head": False,
                    "use_head_distraction": True,
                    "allow_unvalidated_phone_candidates": True,
                },
                "rules": {
                    "drowsiness": {},
                    "distraction": {
                        "likely_phone_interaction": 0.5,
                        "sustained_head_distraction": 1.0,
                    },
                },
            },
            Path.cwd(),
        )

        def evaluate(*, head_active: bool):
            return engine.evaluate(
                eye=EyeObservation(EyeState.OPEN, 0.3, 0.3, 0.3, None),
                closure_fraction=0.0,
                perclos=WindowFeature(0.0, 0.0, 60.0, 1.0, True),
                mouth=MouthCandidateDecision(MouthState.CLOSED, 0.0, False, False, False, None, None),
                head_pose=HeadPoseObservation(False, None, None, None, None, None, None, None, None, "not_needed"),
                interaction=PhoneInteractionObservation("interaction_candidate", True, True, None, 0),
                critical_event_id=None,
                phone_age_s=0.0,
                head_distraction_active=head_active,
                head_distraction_valid=True,
                head_distraction_reasons=("sustained_right_turn",) if head_active else (),
            )

        hand_only = evaluate(head_active=False)
        self.assertNotIn("likely_phone_interaction", hand_only.distraction.reasons)
        self.assertIsNot(hand_only.distraction.state, RiskState.HIGH_CANDIDATE)
        self.assertFalse(hand_only.evidence["distraction"]["likely_phone_interaction"].valid)
        self.assertIsNone(hand_only.evidence["distraction"]["likely_phone_interaction"].value)

        hand_and_head = evaluate(head_active=True)
        self.assertEqual(hand_and_head.distraction.reasons, ("sustained_right_turn",))


if __name__ == "__main__":
    unittest.main()
