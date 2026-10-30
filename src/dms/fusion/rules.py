from __future__ import annotations

from typing import Mapping

from dms.fusion.base import Evidence, RiskDecision, RiskState, critical_decision, supported_signals


class RulesFusion:
    """Auditable threshold rules with a critical-closure bypass."""

    def __init__(self, *, max_evidence_age_s: float = 1.0) -> None:
        self.max_evidence_age_s = max_evidence_age_s

    def evaluate(
        self,
        channel: str,
        evidence: Mapping[str, Evidence],
        *,
        positive_thresholds: Mapping[str, float],
        critical_closure: bool = False,
        critical_event_id: str | None = None,
    ) -> RiskDecision:
        if channel == "drowsiness" and critical_closure:
            return critical_decision(channel, critical_event_id, "rules")
        supported, unavailable = supported_signals(evidence, max_age_s=self.max_evidence_age_s)
        reasons = tuple(
            name
            for name, threshold in positive_thresholds.items()
            if name in supported and float(supported[name].value) >= threshold
        )
        if reasons:
            state = RiskState.ELEVATED_CANDIDATE
            if any(supported[name].value is not None and supported[name].value >= 0.9 for name in reasons):
                state = RiskState.HIGH_CANDIDATE
            return RiskDecision(channel, state, None, reasons, tuple(supported), unavailable, "rules")
        state = RiskState.NO_TRIGGER if supported else RiskState.UNKNOWN
        return RiskDecision(channel, state, None, (), tuple(supported), unavailable, "rules")
