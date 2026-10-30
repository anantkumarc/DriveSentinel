from __future__ import annotations

import math
from typing import Mapping

from dms.fusion.base import Evidence, RiskDecision, RiskState, critical_decision, supported_signals


class WeightedFusion:
    """Fixed-scale weighted score; missing channels are never renormalized."""

    def __init__(
        self,
        weights: Mapping[str, float],
        *,
        elevated_threshold: float = 0.4,
        high_threshold: float = 0.75,
        min_weight_coverage: float = 0.5,
        max_evidence_age_s: float = 1.0,
    ) -> None:
        if not weights or any(not math.isfinite(w) or w < 0 for w in weights.values()) or sum(weights.values()) <= 0:
            raise ValueError("weights must be finite, nonnegative, and have positive total")
        if not 0 <= elevated_threshold < high_threshold <= 1:
            raise ValueError("weighted thresholds must satisfy 0 <= elevated < high <= 1")
        if not 0 < min_weight_coverage <= 1:
            raise ValueError("minimum weight coverage must be in (0, 1]")
        self.weights = dict(weights)
        self.elevated_threshold = elevated_threshold
        self.high_threshold = high_threshold
        self.min_weight_coverage = min_weight_coverage
        self.max_evidence_age_s = max_evidence_age_s

    def evaluate(
        self,
        channel: str,
        evidence: Mapping[str, Evidence],
        *,
        critical_closure: bool = False,
        critical_event_id: str | None = None,
    ) -> RiskDecision:
        if channel == "drowsiness" and critical_closure:
            return critical_decision(channel, critical_event_id, "weighted")
        supported, unavailable = supported_signals(evidence, max_age_s=self.max_evidence_age_s)
        total_weight = sum(self.weights.values())
        covered_weight = sum(self.weights.get(name, 0.0) * item.quality for name, item in supported.items())
        coverage = covered_weight / total_weight
        if coverage < self.min_weight_coverage:
            return RiskDecision(channel, RiskState.UNKNOWN, None, (), tuple(supported), unavailable, "weighted")
        # The denominator is fixed: missing inputs reduce coverage and never
        # inflate remaining inputs through automatic renormalization.
        score = sum(
            self.weights.get(name, 0.0) * max(0.0, min(1.0, float(item.value))) * item.quality
            for name, item in supported.items()
        ) / total_weight
        if score >= self.high_threshold:
            state = RiskState.HIGH_CANDIDATE
        elif score >= self.elevated_threshold:
            state = RiskState.ELEVATED_CANDIDATE
        else:
            state = RiskState.NO_TRIGGER
        return RiskDecision(channel, state, score, ("fixed_scale_weighted_score",), tuple(supported), unavailable, "weighted")
