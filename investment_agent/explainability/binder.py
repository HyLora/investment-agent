from __future__ import annotations

from investment_agent.domain.models import ActionType, MetricEvidence, Suggestion
from investment_agent.explainability.evidence import EvidenceStore


class ExplainabilityBinder:
    """Binds LLM suggestions to exact MetricEvidence records.

    Rejects or marks invalid any suggestion that cites missing ids or
    (for non-HOLD actions) provides no evidence.
    """

    def bind(self, suggestions: list[Suggestion], store: EvidenceStore) -> list[Suggestion]:
        bound: list[Suggestion] = []
        for suggestion in suggestions:
            bound.append(self._bind_one(suggestion, store))
        return bound

    def _bind_one(self, suggestion: Suggestion, store: EvidenceStore) -> Suggestion:
        notes: list[str] = []
        resolved: list[MetricEvidence] = []
        missing: list[str] = []

        for eid in suggestion.evidence_ids:
            item = store.get(eid)
            if item is None:
                missing.append(eid)
            else:
                resolved.append(item)

        if missing:
            notes.append(f"Unknown evidence_ids: {', '.join(missing)}")

        if suggestion.action != ActionType.HOLD and not suggestion.evidence_ids:
            notes.append("Non-HOLD suggestion without evidence_ids")

        if suggestion.action != ActionType.HOLD and suggestion.evidence_ids and not resolved:
            notes.append("No resolvable MetricEvidence for cited ids")

        # Prefer that at least one cited metric actually triggered
        if suggestion.action != ActionType.HOLD and resolved and not any(e.triggered for e in resolved):
            notes.append("Cited evidence exists but none is above threshold (triggered=false)")

        # BUY/SELL must agree with weight_deviation sign when that metric is cited
        weight = next((e for e in resolved if e.kind.value == "weight_deviation"), None)
        if weight is not None:
            if suggestion.action == ActionType.BUY and weight.value > 0:
                notes.append(
                    f"BUY contradicts overweight weight_deviation={weight.value} pp"
                )
            if suggestion.action == ActionType.SELL and weight.value < 0:
                notes.append(
                    f"SELL contradicts underweight weight_deviation={weight.value} pp"
                )

        valid = len(notes) == 0 and (
            suggestion.action == ActionType.HOLD or (bool(resolved) and any(e.triggered for e in resolved))
        )

        if suggestion.action == ActionType.HOLD and not suggestion.evidence_ids:
            valid = True

        return suggestion.model_copy(
            update={
                "evidence": resolved,
                "explainability_valid": valid,
                "validation_notes": notes,
            }
        )
