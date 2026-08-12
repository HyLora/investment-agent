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

from investment_agent.ai.advisor import AdvisorPort, _parse_suggestions
from investment_agent.domain.models import PortfolioSnapshot
from investment_agent.explainability.evidence import EvidenceStore

OLLAMA_SYSTEM_PROMPT = """Sei un advisor ETF in modalità SOLO CONSULENZA (locale).
Non eseguire ordini. Non inventare numeri.

Devi produrre raccomandazioni in italiano in due orizzonti:
- long_term: "Investi oggi su: TICKER ... e lascia per 5+ anni ..."
- short_term: "Per breve termine: investi oggi su: TICKER ... e tienili per 3-6 mesi (o 1-3 mesi se rischio alto); a fine periodo rivaluta ..."

Il 'guadagno' può citare SOLO il period_return storico osservato dalle evidence
(con la frase 'non garanzia futura'). Mai previsioni inventate.

Regole:
1. Usa solo MetricEvidence fornite (scostamento, drawdown, volatilità, period_return).
2. Ogni non-HOLD deve avere evidence_ids esistenti.
3. Rispondi SOLO JSON:
{
  "suggestions": [
    {
      "action": "BUY"|"SELL"|"HOLD"|"REBALANCE",
      "symbol": "TICKER",
      "horizon": "long_term"|"short_term",
      "hold_for": "5+ anni"|"3-6 mesi",
      "headline": "Investi oggi su: ... e lascia per ... (rendimento storico osservato ...% — non garanzia futura)",
      "rationale_text": "scostamento esatto + parametro di rischio",
      "evidence_ids": ["id"],
      "indicative_shares": null,
      "indicative_notional": null,
      "historical_return_pct": null
    }
  ]
}
"""


class OllamaAdvisor(AdvisorPort):
    """Explainable advisor backed by a local Ollama HTTP endpoint."""

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

    def suggest(self, snapshot: PortfolioSnapshot, store: EvidenceStore):
        """Ask the local LLM for grounded rebalancing suggestions."""
        payload = {
            "portfolio": snapshot.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in store.all()],
            "triggered_evidence_ids": [e.evidence_id for e in store.triggered()],
            "instructions": (
                "Produce long_term and short_term headlines. "
                "Do not invent metrics. Quote exact deviation and risk from evidence. "
                "historical_return_pct only from period_return evidence."
            ),
        }
        content = self._chat(payload)
        return _parse_suggestions(content)

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


def try_build_ollama_advisor(model: str, base_url: str) -> OllamaAdvisor | None:
    """Return an OllamaAdvisor if the local daemon responds to /api/tags."""
    try:
        request = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(request, timeout=2.0):
            return OllamaAdvisor(model=model, base_url=base_url)
    except Exception:
        return None
