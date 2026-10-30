"""JSON-backed calibrated logistic model with strict feature schema checks."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping

from dms.fusion.base import Evidence, RiskDecision, RiskState, critical_decision


class CalibratedFusion:
    def __init__(
        self,
        model_path: str | Path,
        *,
        expected_schema_version: int = 1,
        max_evidence_age_s: float = 1.0,
    ) -> None:
        self.path = Path(model_path)
        if not math.isfinite(max_evidence_age_s) or max_evidence_age_s <= 0:
            raise ValueError("maximum calibrated evidence age must be positive")
        self.max_evidence_age_s = max_evidence_age_s
        with self.path.open("r", encoding="utf-8") as handle:
            model = json.load(handle)
        if not isinstance(model, dict):
            raise ValueError("calibrated fusion model must be a JSON object")
        if model.get("schema_version") != expected_schema_version:
            raise ValueError("calibrated fusion model schema version mismatch")
        self.feature_names = tuple(model.get("feature_names", ()))
        if not self.feature_names or len(self.feature_names) != len(set(self.feature_names)):
            raise ValueError("calibrated fusion model has invalid feature_names")
        self.means = tuple(float(v) for v in model["means"])
        self.scales = tuple(float(v) for v in model["scales"])
        self.coefficients = tuple(float(v) for v in model["coefficients"])
        self.intercept = float(model["intercept"])
        self.calibration_slope = float(model.get("calibration_slope", 1.0))
        self.calibration_intercept = float(model.get("calibration_intercept", 0.0))
        self.elevated_threshold = float(model.get("elevated_threshold", 0.5))
        self.high_threshold = float(model.get("high_threshold", 0.8))
        lengths = {len(self.feature_names), len(self.means), len(self.scales), len(self.coefficients)}
        numeric_parameters = (
            *self.means,
            *self.scales,
            *self.coefficients,
            self.intercept,
            self.calibration_slope,
            self.calibration_intercept,
            self.elevated_threshold,
            self.high_threshold,
        )
        if len(lengths) != 1 or any(not math.isfinite(value) for value in numeric_parameters) or any(scale <= 0 for scale in self.scales):
            raise ValueError("calibrated fusion arrays have inconsistent lengths or invalid scales")
        if not 0 <= self.elevated_threshold < self.high_threshold <= 1:
            raise ValueError("calibrated fusion thresholds are invalid")

    def evaluate(
        self,
        channel: str,
        evidence: Mapping[str, Evidence],
        *,
        critical_closure: bool = False,
        critical_event_id: str | None = None,
    ) -> RiskDecision:
        if channel == "drowsiness" and critical_closure:
            return critical_decision(channel, critical_event_id, "calibrated")
        missing = tuple(
            name for name in self.feature_names
            if name not in evidence or not evidence[name].valid or evidence[name].value is None
        )
        if missing:
            return RiskDecision(channel, RiskState.UNKNOWN, None, (), (), missing, "calibrated")
        raw_logit = self.intercept
        for name, mean, scale, coefficient in zip(self.feature_names, self.means, self.scales, self.coefficients):
            item = evidence[name]
            if item.quality <= 0 or item.age_s < 0 or item.age_s > self.max_evidence_age_s:
                return RiskDecision(channel, RiskState.UNKNOWN, None, (), (), (name,), "calibrated")
            raw_logit += ((float(item.value) - mean) / scale) * coefficient
        calibrated_logit = self.calibration_slope * raw_logit + self.calibration_intercept
        probability = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, calibrated_logit))))
        if probability >= self.high_threshold:
            state = RiskState.HIGH_CANDIDATE
        elif probability >= self.elevated_threshold:
            state = RiskState.ELEVATED_CANDIDATE
        else:
            state = RiskState.NO_TRIGGER
        return RiskDecision(
            channel,
            state,
            probability,
            ("calibrated_logistic_model",),
            self.feature_names,
            (),
            "calibrated",
        )
