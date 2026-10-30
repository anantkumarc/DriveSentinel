from __future__ import annotations

import unittest
from pathlib import Path

from dms.detectors.eyes import EyeObservation, EyeState
from dms.detectors.hands import HandObservation
from dms.detectors.head_pose import HeadPoseObservation
from dms.detectors.mouth import MouthCandidateDecision, MouthState
from dms.detectors.phone_boxes import Box, PhoneDetection
from dms.detectors.phone_interaction import PhoneInteractionEstimator
from dms.fusion.base import RiskState
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


if __name__ == "__main__":
    unittest.main()
