from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any

from investment_agent.domain.models import (
    ActionType,
    MetricEvidence,
    MetricKind,
    PortfolioSnapshot,
    Suggestion,
)
from investment_agent.explainability.evidence import EvidenceStore


class AdvisorPort(ABC):
    backend_name: str

    @abstractmethod
    def suggest(
        self,
        snapshot: PortfolioSnapshot,
        store: EvidenceStore,
    ) -> list[Suggestion]:
        raise NotImplementedError


class RuleBasedAdvisor(AdvisorPort):
    """Deterministic advisor: maps triggered metrics to advisory actions.

    Always cites exact evidence_ids — used as offline default and LLM fallback.
    """

    backend_name = "rule_based"

    def suggest(self, snapshot: PortfolioSnapshot, store: EvidenceStore) -> list[Suggestion]:
        by_symbol: dict[str, list[MetricEvidence]] = {}
        for ev in store.triggered():
            by_symbol.setdefault(ev.symbol, []).append(ev)

        if not by_symbol:
            return [
                Suggestion(
                    action=ActionType.HOLD,
                    symbol="PORTFOLIO",
                    rationale_text="Nessuna metrica oltre soglia: nessun ribilanciamento consigliato.",
                    evidence_ids=[],
                )
            ]

        positions = {p.symbol: p for p in snapshot.positions}
        suggestions: list[Suggestion] = []

        for symbol, evidences in by_symbol.items():
            weight_ev = next((e for e in evidences if e.kind == MetricKind.WEIGHT_DEVIATION), None)
            dd_ev = next((e for e in evidences if e.kind == MetricKind.DRAWDOWN), None)
            ids = [e.evidence_id for e in evidences]
            pos = positions.get(symbol)

            if weight_ev is not None and pos is not None:
                # Positive deviation => overweight => suggest SELL; negative => BUY
                if weight_ev.value > 0:
                    action = ActionType.SELL
                    # indicative shares to move toward target weight
                    target_value = pos.target_weight * snapshot.total_value
                    delta_value = pos.market_value - target_value
                    shares = delta_value / pos.price if pos.price else None
                    rationale = (
                        f"{symbol} è sovrappesato di {weight_ev.value:.2f} pp rispetto al target "
                        f"(soglia {weight_ev.threshold} pp). Valutare riduzione verso il peso obiettivo."
                    )
                else:
                    action = ActionType.BUY
                    target_value = pos.target_weight * snapshot.total_value
                    delta_value = target_value - pos.market_value
                    shares = delta_value / pos.price if pos.price else None
                    rationale = (
                        f"{symbol} è sottopesato di {abs(weight_ev.value):.2f} pp rispetto al target "
                        f"(soglia {weight_ev.threshold} pp). Valutare incremento verso il peso obiettivo."
                    )
                if dd_ev is not None:
                    rationale += (
                        f" Drawdown massimo osservato: {dd_ev.value:.2f}% "
                        f"(soglia {dd_ev.threshold}%)."
                    )
                suggestions.append(
                    Suggestion(
                        action=action,
                        symbol=symbol,
                        rationale_text=rationale,
                        evidence_ids=ids,
                        indicative_shares=round(shares, 4) if shares is not None else None,
                        indicative_notional=round(abs(delta_value), 2) if pos else None,
                    )
                )
            elif dd_ev is not None:
                suggestions.append(
                    Suggestion(
                        action=ActionType.REBALANCE,
                        symbol=symbol,
                        rationale_text=(
                            f"{symbol} ha un drawdown di {dd_ev.value:.2f}% "
                            f"(soglia {dd_ev.threshold}%). Rivedere l'allocazione; "
                            "nessun ordine automatico."
                        ),
                        evidence_ids=ids,
                    )
                )
            else:
                suggestions.append(
                    Suggestion(
                        action=ActionType.REBALANCE,
                        symbol=symbol,
                        rationale_text=f"Metriche oltre soglia per {symbol}: valutare ribilanciamento.",
                        evidence_ids=ids,
                    )
                )

        return suggestions


class LLMAdvisor(AdvisorPort):
    """LLM advisor that must cite evidence_ids from the store.

    Numbers in the user-facing report still come from MetricEvidence via the binder,
    not from free-form LLM text.
    """

    backend_name = "llm"

    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key or os.getenv("IA_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.model = model
        if not self.api_key:
            raise ValueError("LLMAdvisor requires IA_OPENAI_API_KEY or OPENAI_API_KEY")

    def suggest(self, snapshot: PortfolioSnapshot, store: EvidenceStore) -> list[Suggestion]:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        payload = self._build_payload(snapshot, store)
        response = client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return self._parse(content)

    def _build_payload(self, snapshot: PortfolioSnapshot, store: EvidenceStore) -> dict[str, Any]:
        return {
            "portfolio": snapshot.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in store.all()],
            "triggered_evidence_ids": [e.evidence_id for e in store.triggered()],
            "instructions": (
                "Propose advisory rebalancing only. Do not invent metrics. "
                "Every non-HOLD suggestion MUST include evidence_ids from the provided list. "
                "Do not claim orders will be executed."
            ),
        }

    def _parse(self, content: str) -> list[Suggestion]:
        data = json.loads(content)
        raw_items = data.get("suggestions", data if isinstance(data, list) else [])
        suggestions: list[Suggestion] = []
        for item in raw_items:
            suggestions.append(
                Suggestion(
                    action=ActionType(item["action"]),
                    symbol=str(item["symbol"]).upper(),
                    rationale_text=str(item["rationale_text"]),
                    evidence_ids=list(item.get("evidence_ids") or []),
                    indicative_shares=item.get("indicative_shares"),
                    indicative_notional=item.get("indicative_notional"),
                )
            )
        return suggestions


SYSTEM_PROMPT = """Sei un advisor di portafoglio ETF. Rispondi SOLO con JSON:
{
  "suggestions": [
    {
      "action": "BUY"|"SELL"|"HOLD"|"REBALANCE",
      "symbol": "TICKER",
      "rationale_text": "testo breve in italiano",
      "evidence_ids": ["id1", ...],
      "indicative_shares": null o numero,
      "indicative_notional": null o numero
    }
  ]
}
Regole:
- Ogni azione diversa da HOLD deve citare evidence_ids esistenti.
- Non inventare valori numerici: riferisciti alle metriche fornite.
- Non eseguire né simulare l'esecuzione di ordini.
- Se nessuna metrica è triggered, restituisci un solo HOLD su PORTFOLIO.
"""


def build_advisor(prefer_llm: bool, settings_api_key: str | None, model: str) -> AdvisorPort:
    if prefer_llm:
        try:
            return LLMAdvisor(api_key=settings_api_key, model=model)
        except Exception:
            return RuleBasedAdvisor()
    return RuleBasedAdvisor()
