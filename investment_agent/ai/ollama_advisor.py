"""Local LLM advisor via Ollama (no cloud API keys, no bank credentials).

The model receives pre-computed :class:`MetricEvidence` only. It must not
invent numbers; every non-HOLD suggestion must cite ``evidence_ids`` and
mention the exact deviation / risk parameter from those metrics.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from investment_agent.ai.advisor import AdvisorPort
from investment_agent.domain.models import ActionType, PortfolioSnapshot, Suggestion
from investment_agent.explainability.evidence import EvidenceStore

OLLAMA_SYSTEM_PROMPT = """Sei un advisor di portafoglio ETF in modalità SOLO CONSULENZA.
Operi in locale. Non eseguire ordini. Non inventare numeri.

Ricevi metriche già calcolate (MetricEvidence). Il tuo compito è SOLO narrare
raccomandazioni di ribilanciamento altamente spiegabili.

Regole OBBLIGATORIE:
1. NON calcolare scostamenti, drawdown o volatilità: usa solo i valori forniti.
2. Ogni suggerimento diverso da HOLD DEVE includere evidence_ids esistenti.
3. Nel rationale_text DEVI menzionare esplicitamente:
   - lo scostamento percentuale esatto (valore + unità dalla evidence), e
   - il parametro di rischio che lo giustifica (drawdown e/o volatilità se presenti).
4. Se nessuna metrica è triggered, restituisci un solo HOLD su PORTFOLIO.
5. Rispondi SOLO con JSON:
{
  "suggestions": [
    {
      "action": "BUY"|"SELL"|"HOLD"|"REBALANCE",
      "symbol": "TICKER",
      "rationale_text": "testo in italiano con numeri citati dalle evidence",
      "evidence_ids": ["id1", ...],
      "indicative_shares": null o numero,
      "indicative_notional": null o numero
    }
  ]
}
"""


class OllamaAdvisor(AdvisorPort):
    """Explainable advisor backed by a local Ollama HTTP endpoint.

    Default base URL is ``http://127.0.0.1:11434`` — no credentials required.
    """

    backend_name = "ollama"

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://127.0.0.1:11434",
        timeout_sec: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def suggest(self, snapshot: PortfolioSnapshot, store: EvidenceStore) -> list[Suggestion]:
        """Ask the local LLM for grounded rebalancing suggestions."""
        payload = self._build_payload(snapshot, store)
        content = self._chat(payload)
        return self._parse(content)

    def _build_payload(self, snapshot: PortfolioSnapshot, store: EvidenceStore) -> dict[str, Any]:
        return {
            "portfolio": snapshot.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in store.all()],
            "triggered_evidence_ids": [e.evidence_id for e in store.triggered()],
            "instructions": (
                "Propose advisory rebalancing only. Do not invent metrics. "
                "Every non-HOLD suggestion MUST include evidence_ids from the provided list "
                "and MUST quote the exact deviation_pp / drawdown / volatility values. "
                "Do not claim orders will be executed. No bank credentials involved."
            ),
        }

    def _chat(self, user_payload: dict[str, Any]) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_payload, default=str)},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise ConnectionError(
                f"Ollama non raggiungibile su {self.base_url}. "
                "Avvia Ollama in locale oppure usa il fallback rule-based."
            ) from exc
        message = raw.get("message") or {}
        return message.get("content") or "{}"

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


def try_build_ollama_advisor(model: str, base_url: str) -> OllamaAdvisor | None:
    """Return an OllamaAdvisor if the local daemon responds to /api/tags."""
    try:
        request = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(request, timeout=2.0):
            return OllamaAdvisor(model=model, base_url=base_url)
    except Exception:
        return None
