from __future__ import annotations

import unittest
from pathlib import Path

from dms.detectors.eyes import EyeObservation, EyeState
from dms.detectors.hands import HandObservation
from dms.detectors.head_pose import HeadPoseObservation
from dms.detectors.mouth import MouthCandidateDecision, MouthState
from dms.detectors.phone_boxes import Box, PhoneDetection
from dms.detectors.phone_interaction import PhoneInteractionEstimator
from dms.alerts.states import AlertLevel, AlertStateMachine
from dms.fusion.base import RiskDecision, RiskState
from dms.fusion.engine import RiskFusionEngine
from dms.temporal.windows import WindowFeature


class PhonePresenceAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.estimator = PhoneInteractionEstimator([0.0, 0.0, 1.0, 1.0])
        self.engine = RiskFusionEngine(
            {
                "method": "rules",
                "channels": {"alert_on_phone_presence": True},
                "rules": {"drowsiness": {}, "distraction": {}},
            },
            Path.cwd(),
        )

    def decide(self, hands: HandObservation):
        interaction = self.estimator.observe(
            PhoneDetection(True, (Box(0.45, 0.4, 0.62, 0.68, 0.92, 67),), None),
            hands,
        )
        return self.engine.evaluate(
            eye=EyeObservation(EyeState.OPEN, 0.3, 0.3, 0.3, None),
            closure_fraction=0.0,
            perclos=WindowFeature(0.0, 0.0, 60.0, 1.0, True),
            mouth=MouthCandidateDecision(MouthState.CLOSED, 0.0, False, False, False, None, None),
            head_pose=HeadPoseObservation(False, None, None, None, None, None, None, None, None, "not_needed"),
            interaction=interaction,
            critical_event_id=None,
            phone_age_s=0.0,
        )

    def test_phone_in_driver_view_warns_even_when_no_hand_box_is_detected(self) -> None:
        output = self.decide(HandObservation(True, (), None))

        self.assertIs(output.distraction.state, RiskState.HIGH_CANDIDATE)
        self.assertIn("phone_presence", output.distraction.reasons)
        self.assertEqual(output.evidence["distraction"]["phone_presence"].value, 1.0)

    def test_phone_presence_still_warns_when_hand_detector_is_unavailable(self) -> None:
        output = self.decide(HandObservation(False, (), "hand_model_dropout"))

        self.assertIs(output.distraction.state, RiskState.HIGH_CANDIDATE)
        self.assertIn("phone_presence", output.distraction.reasons)

    def test_brief_phone_detection_does_not_activate_the_alert(self) -> None:
        machine = AlertStateMachine(
            "distraction", entry_persistence_s=0.0, recovery_persistence_s=0.5, max_sample_gap_s=2.0
        )
        phone = RiskDecision(
            "distraction", RiskState.HIGH_CANDIDATE, 1.0, ("phone_presence",), (), (), "rules"
        )
        clear = RiskDecision("distraction", RiskState.NO_TRIGGER, 0.0, (), (), (), "rules")

        self.assertIs(
            machine.update(phone, 0.0, entry_persistence_override_s=1.0).level,
            AlertLevel.NONE,
        )
        self.assertIs(machine.update(clear, 0.2).level, AlertLevel.NONE)
        self.assertIs(
            machine.update(phone, 0.4, entry_persistence_override_s=1.0).level,
            AlertLevel.NONE,
        )

    def test_confirmed_phone_alerts_after_persistence_but_head_alert_stays_immediate(self) -> None:
        machine = AlertStateMachine(
            "distraction", entry_persistence_s=0.0, recovery_persistence_s=0.5, max_sample_gap_s=2.0
        )
        phone = RiskDecision(
            "distraction", RiskState.HIGH_CANDIDATE, 1.0, ("phone_presence",), (), (), "rules"
        )
        head = RiskDecision(
            "distraction", RiskState.HIGH_CANDIDATE, 1.0, ("sustained_right_turn",), (), (), "rules"
        )
        self.assertIs(
            machine.update(phone, 0.0, entry_persistence_override_s=1.0).level,
            AlertLevel.NONE,
        )
        self.assertIs(
            machine.update(phone, 0.5, entry_persistence_override_s=1.0).level,
            AlertLevel.NONE,
        )
        self.assertIs(
            machine.update(phone, 1.0, entry_persistence_override_s=1.0).level,
            AlertLevel.HIGH,
        )

        head_machine = AlertStateMachine(
            "distraction", entry_persistence_s=1.0, max_sample_gap_s=2.0
        )
        self.assertIs(
            head_machine.update(head, 0.0, entry_persistence_override_s=0.0).level,
            AlertLevel.HIGH,
        )


if __name__ == "__main__":
    unittest.main()
