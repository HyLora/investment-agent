from __future__ import annotations

from investment_agent.domain.models import MetricEvidence


class EvidenceStore:
    """Registry of deterministic metrics available to ground LLM suggestions."""

    def __init__(self, evidence: list[MetricEvidence] | None = None) -> None:
        self._by_id: dict[str, MetricEvidence] = {}
        if evidence:
            self.extend(evidence)

    def extend(self, evidence: list[MetricEvidence]) -> None:
        for item in evidence:
            if item.evidence_id in self._by_id:
                raise ValueError(f"Duplicate evidence_id: {item.evidence_id}")
            self._by_id[item.evidence_id] = item

    def get(self, evidence_id: str) -> MetricEvidence | None:
        return self._by_id.get(evidence_id)

    def require(self, evidence_id: str) -> MetricEvidence:
        item = self.get(evidence_id)
        if item is None:
            raise KeyError(f"Unknown evidence_id: {evidence_id}")
        return item

    def all(self) -> list[MetricEvidence]:
        return list(self._by_id.values())

    def triggered(self) -> list[MetricEvidence]:
        return [e for e in self._by_id.values() if e.triggered]

    def ids(self) -> set[str]:
        return set(self._by_id)
