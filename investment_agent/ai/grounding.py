"""Ground LLM suggestions to deterministic metrics and a locked action plan.

Ollama may only narrate; actions, sizes and evidence ids come from the
deterministic plan (RuleBasedAdvisor). This prevents contradictions such as
BUY on an overweight ticker.
"""

from __future__ import annotations

from investment_agent.ai.advisor import RuleBasedAdvisor
from investment_agent.domain.models import (
    ActionType,
    MetricKind,
    PortfolioSnapshot,
    Suggestion,
)
from investment_agent.explainability.evidence import EvidenceStore


def build_locked_plan(snapshot: PortfolioSnapshot, store: EvidenceStore) -> list[Suggestion]:
    """Deterministic BUY/SELL/amounts/headlines grounded on MetricEvidence."""
    return RuleBasedAdvisor().suggest(snapshot, store)


def merge_ollama_narration(
    plan: list[Suggestion],
    llm_suggestions: list[Suggestion],
) -> list[Suggestion]:
    """Keep locked plan fields; optionally adopt Ollama rationale/headline wording.

    Never lets the LLM change action, symbol, evidence_ids, notionals, or horizon.
    """
    by_key = {(s.symbol, s.horizon, s.action): s for s in llm_suggestions}
    merged: list[Suggestion] = []
    for item in plan:
        key = (item.symbol, item.horizon, item.action)
        llm = by_key.get(key)
        updates: dict = {}
        if llm is not None:
            if llm.rationale_text and _rationale_looks_grounded(llm.rationale_text, item):
                updates["rationale_text"] = llm.rationale_text
            # Prefer locked headline (has exact EUR/shares); keep LLM only if empty plan headline
            if not item.headline and llm.headline:
                updates["headline"] = llm.headline
        merged.append(item.model_copy(update=updates) if updates else item)
    return merged


def _rationale_looks_grounded(text: str, plan_item: Suggestion) -> bool:
    """Reject LLM rationales that clearly contradict the locked action."""
    lower = text.lower()
    if plan_item.action == ActionType.BUY and any(
        tok in lower for tok in ("sovrappes", "riduci", "vendi", "sell")
    ):
        return False
    if plan_item.action == ActionType.SELL and any(
        tok in lower for tok in ("sottopes", "compra", "investi oggi", "buy")
    ):
        return False
    return True


def action_contradicts_weight(suggestion: Suggestion) -> bool:
    """True if BUY/SELL conflicts with cited weight_deviation sign."""
    weight = next(
        (e for e in suggestion.evidence if e.kind == MetricKind.WEIGHT_DEVIATION),
        None,
    )
    if weight is None:
        return False
    if suggestion.action == ActionType.BUY and weight.value > 0:
        return True
    if suggestion.action == ActionType.SELL and weight.value < 0:
        return True
    return False
