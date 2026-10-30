from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dms.detectors.eyes import EyeObservation, EyeState
from dms.detectors.head_pose import HeadPoseObservation
from dms.detectors.mouth import MouthCandidateDecision, MouthState
from dms.detectors.phone_interaction import PhoneInteractionObservation
from dms.fusion.base import Evidence, RiskDecision, RiskState
from dms.fusion.calibrated import CalibratedFusion
from dms.fusion.rules import RulesFusion
from dms.fusion.weighted import WeightedFusion
from dms.temporal.windows import WindowFeature


@dataclass(frozen=True, slots=True)
class FusionOutput:
    drowsiness: RiskDecision
    distraction: RiskDecision
    evidence: dict[str, dict[str, Evidence]]


class RiskFusionEngine:
    """Build separate evidence channels and apply one selected strategy."""

    def __init__(self, config: Mapping[str, Any], project_root: Path) -> None:
        self.config = dict(config)
        self.project_root = project_root
        method = str(config.get("method", "rules"))
        if method not in {"rules", "weighted", "calibrated"}:
            raise ValueError("fusion method must be rules, weighted, or calibrated")
        self.method = method
        self.max_age_s = float(config.get("max_evidence_age_seconds", 1.0))
        self.rules = RulesFusion(max_evidence_age_s=self.max_age_s)
        self.weighted: dict[str, WeightedFusion] = {}
        if method == "weighted":
            for channel in ("drowsiness", "distraction"):
                options = config.get("weighted", {}).get(channel, {})
                self.weighted[channel] = WeightedFusion(
                    options.get("weights", {}),
                    elevated_threshold=float(options.get("elevated_threshold", 0.4)),
                    high_threshold=float(options.get("high_threshold", 0.75)),
                    min_weight_coverage=float(options.get("min_weight_coverage", 0.5)),
                    max_evidence_age_s=self.max_age_s,
                )
        self.calibrated: dict[str, CalibratedFusion | None] = {"drowsiness": None, "distraction": None}
        self.calibration_errors: dict[str, str] = {}
        if method == "calibrated":
            for channel in ("drowsiness", "distraction"):
                model_value = config.get("calibrated", {}).get(channel)
                if not model_value:
                    self.calibration_errors[channel] = "calibrated model path not configured"
                    continue
                model_path = Path(model_value)
                if not model_path.is_absolute():
                    model_path = project_root / model_path
                try:
                    self.calibrated[channel] = CalibratedFusion(
                        model_path,
                        max_evidence_age_s=self.max_age_s,
                    )
                except (FileNotFoundError, ValueError, OSError) as exc:
                    self.calibration_errors[channel] = str(exc)

    def evaluate(
        self,
        *,
        eye: EyeObservation,
        closure_fraction: float | None,
        perclos: WindowFeature,
        mouth: MouthCandidateDecision,
        head_pose: HeadPoseObservation,
        interaction: PhoneInteractionObservation,
        critical_event_id: str | None,
        phone_age_s: float | None = None,
        head_distraction_active: bool = False,
        head_distraction_valid: bool = False,
        head_distraction_reasons: tuple[str, ...] = (),
    ) -> FusionOutput:
        options = self.config.get("channels", {})
        rules_options = self.config.get("rules", {})
        drowsy: dict[str, Evidence] = {
            "eye_closure_fraction": Evidence(
                "eye_closure_fraction",
                closure_fraction,
                closure_fraction is not None and eye.state is not EyeState.UNKNOWN,
                validated=False,
                reason=eye.reason,
            ),
            "perclos": Evidence(
                "perclos",
                perclos.perclos,
                perclos.valid and perclos.perclos is not None,
                quality=perclos.coverage,
                validated=False,
                reason=None if perclos.valid else "insufficient_eye_window_coverage",
            ),
            "yawn_candidate": Evidence(
                "yawn_candidate",
                1.0 if mouth.candidate_active else 0.0,
                bool(options.get("use_mouth", False)) and mouth.state is not MouthState.UNKNOWN,
                validated=False,
                reason="opening_candidate_only",
            ),
            "head_deviation": self._head_evidence(head_pose, options),
        }
        if not options.get("use_mouth", False):
            drowsy["yawn_candidate"] = Evidence("yawn_candidate", None, False, reason="channel_disabled")
        if not options.get("use_head", False):
            drowsy["head_deviation"] = Evidence("head_deviation", None, False, reason="channel_disabled")

        distraction = {
            "likely_phone_interaction": Evidence(
                "likely_phone_interaction",
                None,
                False,
                validated=False,
                # Phone/hand association remains in separate diagnostics, but
                # hand landmarks or their movement are never alarm evidence.
                reason="hand_interaction_excluded_from_alarm_evidence",
            ),
            "phone_presence": Evidence(
                "phone_presence",
                1.0 if interaction.phone_present_in_roi else 0.0 if interaction.phone_presence_valid else None,
                interaction.phone_presence_valid
                and phone_age_s is not None
                and phone_age_s <= self.max_age_s,
                age_s=phone_age_s or 0.0,
                validated=False,
                reason=None if interaction.phone_presence_valid else "phone_presence_unavailable",
            ),
            "sustained_head_distraction": Evidence(
                "sustained_head_distraction",
                1.0 if head_distraction_active else 0.0 if head_distraction_valid else None,
                bool(options.get("use_head_distraction", False))
                and (head_distraction_valid or head_distraction_active),
                validated=False,
                reason=None if head_distraction_valid or head_distraction_active else "head_pose_unavailable",
            ),
        }

        drowsy_thresholds = rules_options.get("drowsiness", {})
        distraction_thresholds = rules_options.get("distraction", {})
        drowsiness_decision = self._evaluate_channel(
            "drowsiness",
            drowsy,
            drowsy_thresholds,
            critical=closure_fraction is not None and closure_fraction >= 1.0,
            critical_event_id=critical_event_id,
        )
        distraction_decision = self._evaluate_channel("distraction", distraction, distraction_thresholds)
        direct_reasons: list[str] = []
        if head_distraction_active and options.get("use_head_distraction", False):
            # A sustained directional event is a direct HIGH event. It must
            # remain asserted through brief pose loss and is not a mere vote
            # that can be diluted by unrelated, currently negative evidence.
            direct_reasons.extend(head_distraction_reasons or ("sustained_head_distraction",))
        phone_presence = distraction["phone_presence"]
        if (
            options.get("alert_on_phone_presence", False)
            and phone_presence.valid
            and phone_presence.value is not None
            and phone_presence.value >= 1.0
        ):
            # The configured single-driver view treats a phone in the driver
            # ROI as an immediate warning, even when the hand model misses it.
            direct_reasons.append("phone_presence")
        if direct_reasons:
            distraction_decision = RiskDecision(
                "distraction",
                RiskState.HIGH_CANDIDATE,
                1.0,
                tuple(dict.fromkeys(direct_reasons)),
                tuple(distraction),
                (),
                self.method,
            )
        return FusionOutput(
            drowsiness_decision,
            distraction_decision,
            {"drowsiness": drowsy, "distraction": distraction},
        )

    def _evaluate_channel(
        self,
        channel: str,
        evidence: Mapping[str, Evidence],
        thresholds: Mapping[str, float],
        *,
        critical: bool = False,
        critical_event_id: str | None = None,
    ) -> RiskDecision:
        if self.method == "rules":
            return self.rules.evaluate(
                channel,
                evidence,
                positive_thresholds=thresholds,
                critical_closure=critical,
                critical_event_id=critical_event_id,
            )
        if self.method == "weighted":
            return self.weighted[channel].evaluate(
                channel,
                evidence,
                critical_closure=critical,
                critical_event_id=critical_event_id,
            )
        model = self.calibrated[channel]
        if model is None:
            return RiskDecision(
                channel,
                RiskState.UNKNOWN,
                None,
                (self.calibration_errors.get(channel, "model unavailable"),),
                (),
                tuple(evidence),
                "calibrated",
            )
        return model.evaluate(
            channel,
            evidence,
            critical_closure=critical,
            critical_event_id=critical_event_id,
        )

    @staticmethod
    def _head_evidence(
        pose: HeadPoseObservation,
        options: Mapping[str, Any],
    ) -> Evidence:
        if not pose.valid or pose.relative_yaw_deg is None or pose.relative_pitch_deg is None:
            return Evidence("head_deviation", None, False, reason=pose.reason or "neutral_reference_missing")
        yaw_limit = float(options.get("head_yaw_limit_deg", 20.0))
        pitch_limit = float(options.get("head_pitch_limit_deg", 15.0))
        if yaw_limit <= 0 or pitch_limit <= 0:
            return Evidence("head_deviation", None, False, reason="invalid_head_threshold")
        score = max(abs(pose.relative_yaw_deg) / yaw_limit, abs(pose.relative_pitch_deg) / pitch_limit)
        return Evidence("head_deviation", min(1.0, score), True, validated=False)
