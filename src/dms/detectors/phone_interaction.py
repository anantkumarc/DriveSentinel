"""Phone/hand spatial association; defaults to an unvalidated candidate."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from dms.detectors.hands import HandObservation, NormalizedBox
from dms.detectors.phone_boxes import PhoneDetection


@dataclass(frozen=True, slots=True)
class PhoneInteractionObservation:
    state: str
    candidate: bool
    validated: bool
    reason: str | None
    associated_box_index: int | None
    phone_present_in_roi: bool = False
    phone_presence_valid: bool = False


class PhoneInteractionEstimator:
    """Associate phone and hand boxes inside a declared driver-seat ROI.

    Geometric proximity is only an interaction candidate. This class never
    infers use from phone presence alone. A config may allow an unvalidated
    phone/hand candidate to raise an exploratory warning; set
    ``association_validated`` only after separate annotated validation.
    """

    def __init__(
        self,
        driver_roi: Sequence[float] | None,
        *,
        proximity_fraction: float = 0.06,
        association_validated: bool = False,
    ) -> None:
        if driver_roi is not None:
            if len(driver_roi) != 4 or not (0 <= driver_roi[0] < driver_roi[2] <= 1 and 0 <= driver_roi[1] < driver_roi[3] <= 1):
                raise ValueError("driver ROI must be normalized [left, top, right, bottom]")
        if proximity_fraction < 0:
            raise ValueError("proximity fraction must be nonnegative")
        self.driver_roi = None if driver_roi is None else tuple(float(v) for v in driver_roi)
        self.proximity_fraction = proximity_fraction
        self.association_validated = association_validated

    def observe(self, phones: PhoneDetection, hands: HandObservation) -> PhoneInteractionObservation:
        if self.driver_roi is None:
            return PhoneInteractionObservation("unknown", False, False, "driver_roi_not_configured", None)
        if not phones.valid:
            return PhoneInteractionObservation("unknown", False, False, phones.reason or "phone_observation_unknown", None)
        phone_indices = [
            index for index, phone in enumerate(phones.boxes)
            if self._inside_roi(phone, self.driver_roi)
        ]
        phone_present = bool(phone_indices)
        if not hands.valid:
            return PhoneInteractionObservation(
                "unknown", False, False, hands.reason or "hand_observation_unknown", None,
                phone_present, True,
            )
        if not phone_indices or not hands.boxes:
            return PhoneInteractionObservation(
                "no_candidate", False, self.association_validated, None, None,
                phone_present, True,
            )

        for index in phone_indices:
            phone = phones.boxes[index]
            for hand in hands.boxes:
                if self._near(phone, hand):
                    state = "likely_use_candidate" if self.association_validated else "interaction_candidate"
                    return PhoneInteractionObservation(
                        state, True, self.association_validated, None, index,
                        phone_present, True,
                    )
        return PhoneInteractionObservation(
            "no_candidate", False, self.association_validated, None, None,
            phone_present, True,
        )

    def _near(self, phone, hand: NormalizedBox) -> bool:
        overlap_w = max(0.0, min(phone.x2, hand.x2) - max(phone.x1, hand.x1))
        overlap_h = max(0.0, min(phone.y2, hand.y2) - max(phone.y1, hand.y1))
        overlap = overlap_w * overlap_h
        smaller = min((phone.x2 - phone.x1) * (phone.y2 - phone.y1), (hand.x2 - hand.x1) * (hand.y2 - hand.y1))
        if smaller > 0 and overlap / smaller >= 0.1:
            return True
        pcx, pcy = (phone.x1 + phone.x2) / 2, (phone.y1 + phone.y2) / 2
        hcx, hcy = (hand.x1 + hand.x2) / 2, (hand.y1 + hand.y2) / 2
        return math.hypot(pcx - hcx, pcy - hcy) <= self.proximity_fraction

    @staticmethod
    def _inside_roi(box, roi: tuple[float, float, float, float]) -> bool:
        left, top, right, bottom = roi
        cx, cy = (box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2
        return left <= cx <= right and top <= cy <= bottom
